//! The Python engine (`vedit-engine serve`) as a child process: spawn, wait for `@@ready {"port": N}`, kill on exit.
//!
//! The bearer token goes only through env VEDIT_ENGINE_TOKEN (argv is readable by other processes). On Windows the
//! engine is put in a Job Object with KILL_ON_JOB_CLOSE held for the app's lifetime, so even a crash or a forced kill
//! of the app takes the engine and everything under it down. In debug builds, VEDIT_DEV_ENGINE=1 runs
//! scripts/engine_entry.py from the repo with the system Python instead of the bundled sidecar.

use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::future::Future;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::mpsc::Receiver;

pub const START_FAILED: &str = "engine_start_failed";
const READY_TIMEOUT: Duration = Duration::from_secs(30);
const STDERR_TAIL_LINES: usize = 20;
const ENGINE_SCRIPT: &str = "scripts/engine_entry.py";

#[derive(Clone, Serialize)]
pub struct EngineInfo {
    pub port: u16,
    pub token: String,
}

/// Debug without the token, so it can never reach a log through `{:?}`.
impl std::fmt::Debug for EngineInfo {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EngineInfo").field("port", &self.port).field("token", &"<redacted>").finish()
    }
}

/// Arguments after the executable (sidecar) or after the script path (dev). No secrets here.
pub fn engine_args() -> Vec<String> {
    ["serve", "--port", "0"].map(String::from).to_vec()
}

/// Environment for the engine: the token, plus VEDIT_FFMPEG_DIR when the bundled ffmpeg folder exists.
pub fn engine_env(token: &str, ffmpeg_dir: Option<PathBuf>) -> Vec<(String, String)> {
    let mut env = vec![("VEDIT_ENGINE_TOKEN".to_string(), token.to_string())];
    if let Some(dir) = ffmpeg_dir {
        env.push(("VEDIT_FFMPEG_DIR".to_string(), dir.to_string_lossy().into_owned()));
    }
    env
}

/// Port from the engine's `@@ready {"port": N}` stdout line; None for any other line.
pub fn parse_ready(line: &str) -> Option<u16> {
    #[derive(serde::Deserialize)]
    struct Ready {
        port: u16,
    }
    let json = line.trim_end().strip_prefix("@@ready ")?;
    let ready: Ready = serde_json::from_str(json).ok()?;
    (ready.port != 0).then_some(ready.port)
}

/// 32 random bytes as 64 lowercase hex characters.
pub fn new_token() -> Result<String, getrandom::Error> {
    let mut bytes = [0u8; 32];
    getrandom::fill(&mut bytes)?;
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

/// Nearest ancestor of `start` (inclusive) that holds scripts/engine_entry.py.
pub fn find_repo_root(start: &Path) -> Option<PathBuf> {
    start.ancestors().find(|d| d.join(ENGINE_SCRIPT).is_file()).map(Path::to_path_buf)
}

/// VEDIT_DEV_ENGINE=1 (system Python on the repo's scripts) is honoured only in debug builds.
fn dev_engine_enabled() -> bool {
    cfg!(debug_assertions) && std::env::var("VEDIT_DEV_ENGINE").is_ok_and(|v| v == "1")
}

fn dev_repo_root() -> Option<PathBuf> {
    find_repo_root(Path::new(env!("CARGO_MANIFEST_DIR")))
        .or_else(|| std::env::current_exe().ok().and_then(|exe| find_repo_root(&exe)))
}

/// An engine that printed `@@ready`. `alive` is cleared once its process exits.
pub struct Started {
    pub info: EngineInfo,
    pub alive: Arc<AtomicBool>,
}

pub struct Engine {
    /// Held across a start, so concurrent callers share one. Holds only a successful start.
    running: tokio::sync::Mutex<Option<Started>>,
    child: Mutex<Option<CommandChild>>,
    stopping: AtomicBool,
    #[cfg(windows)]
    job: Option<job::Job>,
}

impl Engine {
    pub fn new() -> Self {
        Engine {
            running: tokio::sync::Mutex::new(None),
            child: Mutex::new(None),
            stopping: AtomicBool::new(false),
            #[cfg(windows)]
            job: job::Job::new()
                .map_err(|e| eprintln!("[vedit] could not create the engine job object: {e}"))
                .ok(),
        }
    }

    /// Port and token of the running engine, starting it if needed. A failed start is not kept (the next call
    /// starts fresh) and an engine that died after ready is restarted.
    pub async fn info(&self, app: &AppHandle) -> Result<EngineInfo, String> {
        self.ensure(|| self.start(app)).await
    }

    async fn ensure<F, Fut>(&self, start: F) -> Result<EngineInfo, String>
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = Result<Started, ()>>,
    {
        let mut running = self.running.lock().await;
        if let Some(current) = running.as_ref() {
            if current.alive.load(Ordering::SeqCst) {
                return Ok(current.info.clone());
            }
            eprintln!("[vedit] engine is no longer running; restarting it");
            *running = None;
            self.kill_child();
        }
        if self.stopping.load(Ordering::SeqCst) {
            return Err(START_FAILED.to_string());
        }
        let started = start().await.map_err(|()| START_FAILED.to_string())?;
        let info = started.info.clone();
        *running = Some(started);
        Ok(info)
    }

    async fn start(&self, app: &AppHandle) -> Result<Started, ()> {
        let token = new_token().map_err(|e| eprintln!("[vedit] engine token: {e}"))?;
        let ffmpeg_dir = app.path().resource_dir().ok().map(|d| d.join("bin")).filter(|d| d.is_dir());

        let command = if dev_engine_enabled() {
            let root = dev_repo_root().ok_or_else(|| eprintln!("[vedit] VEDIT_DEV_ENGINE=1 but {ENGINE_SCRIPT} not found"))?;
            eprintln!("[vedit] dev engine: python {}", root.join(ENGINE_SCRIPT).display());
            app.shell()
                .command("python")
                .arg(root.join(ENGINE_SCRIPT))
                .args(engine_args())
                .current_dir(root)
        } else {
            app.shell()
                .sidecar("vedit-engine")
                .map_err(|e| eprintln!("[vedit] engine sidecar: {e}"))?
                .args(engine_args())
        };
        let (rx, child) = command
            .envs(engine_env(&token, ffmpeg_dir))
            .spawn()
            .map_err(|e| eprintln!("[vedit] engine spawn failed: {e}"))?;

        #[cfg(windows)]
        if let Some(job) = &self.job {
            if let Err(e) = job.assign(child.pid()) {
                eprintln!("[vedit] could not put the engine in its job object: {e}");
            }
        }
        *self.child.lock().unwrap_or_else(|e| e.into_inner()) = Some(child);
        if self.stopping.load(Ordering::SeqCst) {
            self.kill_child();
            return Err(());
        }

        match wait_ready(rx).await {
            Ok((port, rx)) => {
                let alive = Arc::new(AtomicBool::new(true));
                tauri::async_runtime::spawn(drain(rx, alive.clone()));
                Ok(Started { info: EngineInfo { port, token }, alive })
            }
            Err(()) => {
                self.kill_child();
                Err(())
            }
        }
    }

    /// Kill the current engine and its whole tree; later starts stay allowed.
    fn kill_child(&self) {
        if let Some(child) = self.child.lock().unwrap_or_else(|e| e.into_inner()).take() {
            let pid = child.pid();
            match child.kill() {
                Ok(()) => eprintln!("[vedit] engine stopped (pid {pid})"),
                Err(e) => eprintln!("[vedit] engine kill (pid {pid}): {e}"),
            }
        }
        #[cfg(windows)]
        if let Some(job) = &self.job {
            job.terminate();
        }
    }

    /// Kill the engine for good. Idempotent; called on main-window close and on RunEvent::Exit.
    pub fn shutdown(&self) {
        self.stopping.store(true, Ordering::SeqCst);
        self.kill_child();
    }
}

/// Reads engine output until `@@ready`, exit, or the 30 s timeout. Stderr is kept as a tail for the log only.
async fn wait_ready(mut rx: Receiver<CommandEvent>) -> Result<(u16, Receiver<CommandEvent>), ()> {
    let deadline = tokio::time::Instant::now() + READY_TIMEOUT;
    let mut tail: VecDeque<String> = VecDeque::new();
    let log_tail = |why: &str, tail: &VecDeque<String>| {
        eprintln!("[vedit] engine start failed: {why}; last stderr lines:");
        for line in tail {
            eprintln!("[engine] {line}");
        }
    };
    loop {
        match tokio::time::timeout_at(deadline, rx.recv()).await {
            Err(_) => {
                log_tail("no @@ready within 30 s", &tail);
                return Err(());
            }
            Ok(None) => {
                log_tail("output closed", &tail);
                return Err(());
            }
            Ok(Some(CommandEvent::Terminated(t))) => {
                log_tail(&format!("exited before ready (code {:?})", t.code), &tail);
                return Err(());
            }
            Ok(Some(CommandEvent::Stdout(bytes))) => {
                let line = String::from_utf8_lossy(&bytes);
                if let Some(port) = parse_ready(&line) {
                    eprintln!("[vedit] engine ready on port {port}");
                    return Ok((port, rx));
                }
                eprintln!("[engine] {}", line.trim_end());
            }
            Ok(Some(CommandEvent::Stderr(bytes))) => {
                if tail.len() == STDERR_TAIL_LINES {
                    tail.pop_front();
                }
                tail.push_back(String::from_utf8_lossy(&bytes).trim_end().to_string());
            }
            Ok(Some(CommandEvent::Error(e))) => eprintln!("[vedit] engine output error: {e}"),
            Ok(Some(_)) => {}
        }
    }
}

/// After ready: keep the engine's output flowing into the app log; clear `alive` when it exits.
async fn drain(mut rx: Receiver<CommandEvent>, alive: Arc<AtomicBool>) {
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(b) | CommandEvent::Stderr(b) => {
                eprintln!("[engine] {}", String::from_utf8_lossy(&b).trim_end())
            }
            CommandEvent::Terminated(t) => {
                alive.store(false, Ordering::SeqCst);
                eprintln!("[vedit] engine exited (code {:?})", t.code)
            }
            _ => {}
        }
    }
    alive.store(false, Ordering::SeqCst);
}

#[cfg(windows)]
mod job {
    use std::ffi::c_void;
    use std::io;

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::{OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE};

    /// A Job Object that kills its processes when its last handle closes (including when this process dies).
    pub struct Job(HANDLE);

    // SAFETY: a job handle is a kernel handle, usable from any thread.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn new() -> io::Result<Job> {
            // SAFETY: plain Win32 calls; the handle is closed on every failure path or owned by the Job.
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    let err = io::Error::last_os_error();
                    CloseHandle(handle);
                    return Err(err);
                }
                Ok(Job(handle))
            }
        }

        pub fn assign(&self, pid: u32) -> io::Result<()> {
            // SAFETY: the process handle is closed before returning.
            unsafe {
                let process = OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid);
                if process.is_null() {
                    return Err(io::Error::last_os_error());
                }
                let ok = AssignProcessToJobObject(self.0, process);
                let err = io::Error::last_os_error();
                CloseHandle(process);
                if ok == 0 {
                    return Err(err);
                }
                Ok(())
            }
        }

        pub fn terminate(&self) {
            // SAFETY: valid handle owned by self.
            unsafe {
                TerminateJobObject(self.0, 1);
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            // SAFETY: valid handle owned by self; closing it kills the job's processes.
            unsafe {
                CloseHandle(self.0);
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::process::{Command, Stdio};
        use std::time::{Duration, Instant};

        fn exits_within(child: &mut std::process::Child, limit: Duration) -> bool {
            let start = Instant::now();
            while start.elapsed() < limit {
                if child.try_wait().unwrap().is_some() {
                    return true;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            false
        }

        fn long_running() -> std::process::Child {
            Command::new("ping")
                .args(["-n", "60", "127.0.0.1"])
                .stdout(Stdio::null())
                .spawn()
                .unwrap()
        }

        #[test]
        fn closing_the_job_kills_its_process() {
            let job = Job::new().unwrap();
            let mut child = long_running();
            job.assign(child.id()).unwrap();
            assert!(!exits_within(&mut child, Duration::from_millis(300)));
            drop(job);
            assert!(exits_within(&mut child, Duration::from_secs(5)), "process outlived its job");
        }

        #[test]
        fn terminate_kills_its_process() {
            let job = Job::new().unwrap();
            let mut child = long_running();
            job.assign(child.id()).unwrap();
            job.terminate();
            assert!(exits_within(&mut child, Duration::from_secs(5)), "process survived terminate");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    #[test]
    fn parse_ready_reads_the_port() {
        assert_eq!(parse_ready("@@ready {\"port\": 51234}"), Some(51234));
        assert_eq!(parse_ready("@@ready {\"port\": 51234}\r\n"), Some(51234));
        assert_eq!(parse_ready("@@ready {\"port\":8080}"), Some(8080));
    }

    #[test]
    fn parse_ready_ignores_other_lines() {
        assert_eq!(parse_ready("noise"), None);
        assert_eq!(parse_ready(""), None);
        assert_eq!(parse_ready("INFO: @@ready {\"port\": 1}"), None);
        assert_eq!(parse_ready("@@ready {\"port\": 70000}"), None);
        assert_eq!(parse_ready("@@ready {\"port\": -1}"), None);
        assert_eq!(parse_ready("@@ready {\"port\": \"1\"}"), None);
        assert_eq!(parse_ready("@@ready {\"port\": 0}"), None);
        assert_eq!(parse_ready("@@ready not json"), None);
    }

    #[test]
    fn engine_args_carry_no_token() {
        assert_eq!(engine_args(), vec!["serve", "--port", "0"]);
    }

    #[test]
    fn engine_env_without_ffmpeg() {
        assert_eq!(
            engine_env("abc", None),
            vec![("VEDIT_ENGINE_TOKEN".to_string(), "abc".to_string())]
        );
    }

    #[test]
    fn engine_env_with_ffmpeg() {
        let dir = PathBuf::from(r"C:\Program Files\Vedit Studio\bin");
        assert_eq!(
            engine_env("abc", Some(dir.clone())),
            vec![
                ("VEDIT_ENGINE_TOKEN".to_string(), "abc".to_string()),
                ("VEDIT_FFMPEG_DIR".to_string(), dir.to_string_lossy().into_owned()),
            ]
        );
    }

    #[test]
    fn tokens_are_64_hex_chars_and_unique() {
        let a = new_token().unwrap();
        let b = new_token().unwrap();
        assert_eq!(a.len(), 64);
        assert!(a.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)));
        assert_ne!(a, b);
    }

    #[test]
    fn repo_root_is_found_from_the_manifest_dir() {
        let root = find_repo_root(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap();
        assert!(root.join(ENGINE_SCRIPT).is_file());
        assert!(find_repo_root(Path::new(env!("CARGO_MANIFEST_DIR")).join("src").as_path()).is_some());
    }

    fn started(port: u16) -> Started {
        Started { info: EngineInfo { port, token: format!("t{port}") }, alive: Arc::new(AtomicBool::new(true)) }
    }

    #[tokio::test]
    async fn failed_start_is_not_kept_and_the_next_call_retries() {
        let engine = Engine::new();
        assert_eq!(engine.ensure(|| async { Err(()) }).await.unwrap_err(), START_FAILED);
        let info = engine.ensure(|| async { Ok(started(4100)) }).await.unwrap();
        assert_eq!(info.port, 4100);
        // Running now: a further call reuses it instead of starting.
        let again = engine.ensure(|| async { panic!("must not start again") }).await.unwrap();
        assert_eq!((again.port, again.token), (4100, "t4100".to_string()));
    }

    #[tokio::test]
    async fn concurrent_calls_share_one_start() {
        let engine = Engine::new();
        let starts = AtomicUsize::new(0);
        let start = || async {
            starts.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(50)).await;
            Ok(started(4200))
        };
        let (a, b, c) = tokio::join!(engine.ensure(start), engine.ensure(start), engine.ensure(start));
        assert_eq!(starts.load(Ordering::SeqCst), 1);
        for info in [a, b, c] {
            assert_eq!(info.unwrap().port, 4200);
        }
    }

    #[tokio::test]
    async fn a_caller_waiting_on_a_failed_start_starts_fresh() {
        let engine = Engine::new();
        let starts = AtomicUsize::new(0);
        let start = || async {
            let n = starts.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(20)).await;
            if n == 0 {
                Err(())
            } else {
                Ok(started(4300))
            }
        };
        let (a, b) = tokio::join!(engine.ensure(start), engine.ensure(start));
        assert_eq!(a.unwrap_err(), START_FAILED);
        assert_eq!(b.unwrap().port, 4300);
        assert_eq!(starts.load(Ordering::SeqCst), 2);
    }

    #[tokio::test]
    async fn a_dead_engine_is_restarted() {
        let engine = Engine::new();
        let first = started(4400);
        let alive = first.alive.clone();
        engine.ensure(|| async { Ok(first) }).await.unwrap();
        alive.store(false, Ordering::SeqCst);
        let info = engine.ensure(|| async { Ok(started(4401)) }).await.unwrap();
        assert_eq!(info.port, 4401);
    }

    #[tokio::test]
    async fn no_start_after_shutdown() {
        let engine = Engine::new();
        engine.shutdown();
        let result = engine.ensure(|| async { panic!("must not start after shutdown") }).await;
        assert_eq!(result.unwrap_err(), START_FAILED);
    }

    #[test]
    fn repo_root_is_none_outside_the_repo() {
        assert_eq!(find_repo_root(&std::env::temp_dir()), None);
    }
}
