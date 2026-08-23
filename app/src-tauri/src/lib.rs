//! Tauri shell: spawns the Python core as a sidecar, reads its one-line JSON
//! handshake ({"port":…,"token":…}) from stdout, injects it into the SPA as
//! `window.__NOOSPHERE__`, and kills the core on exit.
//!
//! v1 runs the core via `uv run noosphere-core` from the repo checkout
//! (frozen-binary sidecar is deferred to packaging — ticket #3). The repo root
//! is resolved from NOOSPHERE_REPO if set, else the compile-time location of
//! this crate (app/src-tauri → ../..).

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct Handshake {
    port: u16,
    token: String,
}

struct CoreProcess(Mutex<Option<Child>>);

fn repo_root() -> PathBuf {
    if let Ok(dir) = std::env::var("NOOSPHERE_REPO") {
        return PathBuf::from(dir);
    }
    // app/src-tauri/../.. == repo root in a dev checkout.
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("repo root not found; set NOOSPHERE_REPO")
}

fn spawn_core() -> (Child, Handshake) {
    let root = repo_root();
    let mut child = Command::new("uv")
        .args(["run", "noosphere-core"])
        .current_dir(&root)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .expect("failed to spawn `uv run noosphere-core` (is uv on PATH?)");

    let stdout = child.stdout.take().expect("core stdout not captured");
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut line = String::new();
        let mut reader = BufReader::new(stdout);
        if reader.read_line(&mut line).is_ok() {
            let _ = tx.send(line);
        }
        // Keep draining so the core never blocks on a full stdout pipe.
        for _ in reader.lines() {}
    });

    let line = rx
        .recv_timeout(Duration::from_secs(120))
        .expect("timed out waiting for core handshake line");
    let parsed: serde_json::Value =
        serde_json::from_str(line.trim()).expect("core handshake was not valid JSON");
    let handshake = Handshake {
        port: parsed["port"].as_u64().expect("handshake missing port") as u16,
        token: parsed["token"]
            .as_str()
            .expect("handshake missing token")
            .to_string(),
    };
    (child, handshake)
}

fn wait_for_api(port: u16) {
    // The handshake line prints before uvicorn binds the socket, and startup
    // (DB open + WAL replay) can take seconds on a large corpus. Creating the
    // window earlier hands the SPA a connection-refused API and broken views.
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let deadline = std::time::Instant::now() + Duration::from_secs(120);
    while std::time::Instant::now() < deadline {
        if std::net::TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    panic!("core API on port {port} never started listening");
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let (child, handshake) = spawn_core();
    wait_for_api(handshake.port);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(CoreProcess(Mutex::new(Some(child))))
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            // Besides the handshake, route external links to the system
            // browser — WKWebView silently drops target=_blank otherwise.
            let inject = format!(
                concat!(
                    "window.__NOOSPHERE__ = {{ port: {port}, token: {token} }};\n",
                    "document.addEventListener('click', (e) => {{\n",
                    "  const a = e.target.closest && e.target.closest('a[href]');\n",
                    "  if (!a) return;\n",
                    "  const url = a.href;\n",
                    "  if (/^https?:/.test(url) && new URL(url).origin !== location.origin) {{\n",
                    "    e.preventDefault();\n",
                    "    window.__TAURI_INTERNALS__.invoke('plugin:opener|open_url', {{ url }});\n",
                    "  }}\n",
                    "}}, true);"
                ),
                port = handshake.port,
                token = serde_json::to_string(&handshake.token).unwrap(),
            );
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Academic Noosphere")
                .inner_size(1280.0, 800.0)
                .initialization_script(&inject)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            let core = app_handle.state::<CoreProcess>();
            let child = core.0.lock().unwrap().take();
            if let Some(mut child) = child {
                // Graceful first: SIGKILL mid-write corrupts the LadybugDB WAL
                // (observed repeatedly). SIGTERM lets uvicorn run its lifespan
                // shutdown (DB close -> WAL checkpoint); SIGKILL only if the
                // core hasn't exited after the grace window.
                let _ = Command::new("kill")
                    .args(["-TERM", &child.id().to_string()])
                    .status();
                let deadline = std::time::Instant::now() + Duration::from_secs(25);
                loop {
                    match child.try_wait() {
                        Ok(Some(_)) => break,
                        _ if std::time::Instant::now() >= deadline => {
                            let _ = child.kill();
                            let _ = child.wait();
                            break;
                        }
                        _ => std::thread::sleep(Duration::from_millis(200)),
                    }
                }
            }
        }
    });
}
