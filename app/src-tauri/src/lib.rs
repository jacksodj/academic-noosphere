//! Tauri shell: spawns the Python core as a sidecar, reads its one-line JSON
//! handshake ({"port":…,"token":…}) from stdout, injects it into the SPA as
//! `window.__NOOSPHERE__`, and kills the core on exit.
//!
//! v1 runs the core via `uv run noosphere-core` from the repo checkout
//! (frozen-binary sidecar is deferred to packaging — ticket #3). The repo root
//! is resolved from NOOSPHERE_REPO if set, else the compile-time location of
//! this crate (app/src-tauri → ../..), else conventional clone locations under
//! $HOME — so a downloaded .app works on machines other than the build box.

use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

struct Handshake {
    port: u16,
    token: String,
}

struct CoreProcess(Mutex<Option<Child>>);

/// Fatal startup error: GUI-launched apps have no terminal, so surface the
/// message in a native alert before exiting (best-effort), instead of dying
/// silently in a panic the user never sees.
fn fatal(msg: &str) -> ! {
    eprintln!("fatal: {msg}");
    let _ = Command::new("osascript")
        .args([
            "-e",
            &format!(
                "display alert \"Academic Noosphere\" message {} as critical",
                serde_json::to_string(msg).unwrap()
            ),
        ])
        .status();
    std::process::exit(1);
}

fn is_repo(dir: &Path) -> bool {
    dir.join("pyproject.toml").is_file() && dir.join("src/noosphere").is_dir()
}

fn repo_root() -> PathBuf {
    if let Ok(dir) = std::env::var("NOOSPHERE_REPO") {
        return PathBuf::from(dir);
    }
    // app/src-tauri/../.. == repo root in a dev checkout; the compile-time
    // path is meaningless on a machine that downloaded the built .app, so
    // fall through to conventional clone locations.
    let mut candidates: Vec<PathBuf> =
        vec![PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")];
    if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
        candidates.push(home.join("Code/academic-noosphere"));
        candidates.push(home.join("academic-noosphere"));
    }
    for dir in candidates {
        if let Ok(dir) = dir.canonicalize() {
            if is_repo(&dir) {
                return dir;
            }
        }
    }
    fatal(
        "Could not find the academic-noosphere repo checkout.\n\n\
         Clone it (git clone https://github.com/jacksodj/academic-noosphere) \
         into ~/Code/academic-noosphere or ~/academic-noosphere, or launch \
         with NOOSPHERE_REPO=/path/to/checkout.",
    );
}

/// Locate `uv`. Finder-launched apps inherit the login PATH only partially
/// (typically /usr/bin:/bin:/usr/sbin:/sbin), so the usual install locations
/// are probed explicitly before trusting PATH.
fn find_uv() -> PathBuf {
    if let Ok(uv) = std::env::var("NOOSPHERE_UV") {
        return PathBuf::from(uv);
    }
    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
        candidates.push(home.join(".local/bin/uv"));
        candidates.push(home.join(".cargo/bin/uv"));
    }
    candidates.push(PathBuf::from("/opt/homebrew/bin/uv"));
    candidates.push(PathBuf::from("/usr/local/bin/uv"));
    for uv in candidates {
        if uv.is_file() {
            return uv;
        }
    }
    // Last resort: whatever PATH the process got.
    PathBuf::from("uv")
}

/// The PyInstaller-frozen sidecar shipped inside the bundle
/// (Contents/Resources/sidecar/noosphere-core/noosphere-core), if present.
/// NOOSPHERE_REPO forces the uv dev path even when a bundled core exists.
fn bundled_core() -> Option<PathBuf> {
    if std::env::var_os("NOOSPHERE_REPO").is_some() {
        return None;
    }
    let exe = std::env::current_exe().ok()?;
    let bin = exe
        .parent()? // Contents/MacOS
        .parent()? // Contents
        .join("Resources/sidecar/noosphere-core/noosphere-core");
    bin.is_file().then_some(bin)
}

fn spawn_core() -> (Child, Handshake) {
    let mut command = if let Some(bin) = bundled_core() {
        // Self-contained mode: no uv, no repo checkout. cwd = $HOME so any
        // relative writes land somewhere harmless.
        let mut c = Command::new(bin);
        if let Some(home) = std::env::var_os("HOME") {
            c.current_dir(home);
        }
        c
    } else {
        let root = repo_root();
        let uv = find_uv();
        let mut c = Command::new(&uv);
        c.args(["run", "noosphere-core"]).current_dir(&root);
        c
    };
    let mut child = match command
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
    {
        Ok(child) => child,
        Err(e) => fatal(&format!(
            "Failed to start the Python core: {e}.\n\n\
             Dev checkouts need uv (curl -LsSf https://astral.sh/uv/install.sh | sh); \
             a downloaded app should have its core at Contents/Resources/sidecar — \
             re-download if it is missing.",
        )),
    };

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

    // First launch on a fresh machine runs `uv sync` implicitly (Python
    // download + full dependency install), so the handshake can legitimately
    // take minutes — hence the generous timeout.
    let line = rx.recv_timeout(Duration::from_secs(600)).unwrap_or_else(|_| {
        fatal(
            "The Python core never produced its startup handshake.\n\n\
             Try running `uv run noosphere-core` from the repo checkout in a \
             terminal to see the underlying error.",
        )
    });
    let parsed: serde_json::Value = serde_json::from_str(line.trim())
        .unwrap_or_else(|_| fatal("The core's startup handshake was not valid JSON."));
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
    fatal(&format!("The core API on port {port} never started listening."));
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // The window opens IMMEDIATELY (no Dock-bounce while the core boots — a
    // frozen sidecar plus DB open can take ~15s cold); the core is spawned on
    // a background thread which injects the handshake and fires
    // `noosphere-ready` once the API is actually listening.
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(CoreProcess(Mutex::new(None)))
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            // Route external links to the system browser — WKWebView silently
            // drops target=_blank otherwise.
            let inject = concat!(
                "document.addEventListener('click', (e) => {\n",
                "  const a = e.target.closest && e.target.closest('a[href]');\n",
                "  if (!a) return;\n",
                "  const url = a.href;\n",
                "  if (/^https?:/.test(url) && new URL(url).origin !== location.origin) {\n",
                "    e.preventDefault();\n",
                "    window.__TAURI_INTERNALS__.invoke('plugin:opener|open_url', { url });\n",
                "  }\n",
                "}, true);"
            );
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Academic Noosphere")
                .inner_size(1280.0, 800.0)
                .initialization_script(inject)
                .build()?;

            let handle = app.handle().clone();
            std::thread::spawn(move || {
                let (child, handshake) = spawn_core();
                *handle.state::<CoreProcess>().0.lock().unwrap() = Some(child);
                wait_for_api(handshake.port);
                if let Some(window) = handle.get_webview_window("main") {
                    let script = format!(
                        "window.__NOOSPHERE__ = {{ port: {}, token: {} }};\n\
                         window.dispatchEvent(new Event('noosphere-ready'));",
                        handshake.port,
                        serde_json::to_string(&handshake.token).unwrap(),
                    );
                    let _ = window.eval(&script);
                }
            });

            // A SIGTERM'd shell must still run the Exit cleanup below —
            // observed orphaning the core (which then holds the DB lock).
            let term_handle = app.handle().clone();
            std::thread::spawn(move || {
                use signal_hook::consts::{SIGINT, SIGTERM};
                let mut signals = signal_hook::iterator::Signals::new([SIGTERM, SIGINT])
                    .expect("signal handler");
                if signals.forever().next().is_some() {
                    term_handle.exit(0);
                }
            });
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
