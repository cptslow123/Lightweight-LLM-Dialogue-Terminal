#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;
use tauri::Manager;

struct Backend(Mutex<Option<Child>>);

fn start_backend() -> Child {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let gui_dir = manifest.parent().expect("GUI directory");
    let (program, script, data_dir) = if cfg!(debug_assertions) {
        (gui_dir.join("..\\.venv\\Scripts\\python.exe"), Some(gui_dir.join("backend.py")), gui_dir.join("data"))
    } else {
        let exe_dir = std::env::current_exe().expect("application executable path").parent().expect("application directory").to_path_buf();
        (exe_dir.join("backend.exe"), None, exe_dir)
    };
    let mut command = Command::new(program);
    if let Some(script) = script { command.arg(script); }
    #[cfg(target_os = "windows")]
    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    command
        .current_dir(gui_dir)
        .env("LIGHT_HARNESS_DATA_DIR", data_dir)
        .env("LH_PORT", "18765")
        .spawn()
        .expect("无法启动 Light Harness 后端")
}

fn main() {
    tauri::Builder::default()
        .manage(Backend(Mutex::new(Some(start_backend()))))
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                if let Some(state) = window.app_handle().try_state::<Backend>() {
                    if let Ok(mut backend) = state.0.lock() {
                        if let Some(child) = backend.as_mut() { let _ = child.kill(); }
                        *backend = None;
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Light Harness");
}
