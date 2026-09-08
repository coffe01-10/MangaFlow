//! Process-tree ownership for the desktop shell.
//!
//! **Windows path (V02-54): suspended creation, assignment before the first
//! instruction, then resume — the `scripts/owned_processes.py` `start_python`
//! discipline.** The helper is created `CREATE_SUSPENDED`, assigned to the
//! root Job Object (`KILL_ON_JOB_CLOSE`) while it still has not executed a
//! single instruction, and only then its initial thread is resumed. The
//! spawn→assign race window of the V02-53B compile-only skeleton is gone:
//! whatever happens (graceful exit, shell crash, timeout), the job handle
//! closing kills the whole tree.
//!
//! **Windows stop semantics: cooperative first, then kill.** A Job Object
//! cannot deliver SIGTERM, so [`OwnedTree::stop`] closes the child's piped
//! stdin — the helper's documented EOF watcher reacts by raising SIGTERM to
//! itself and unwinding uvicorn through the FastAPI lifespan shutdown (see
//! `sidecar/mangaflow_desktop_helper.py`, `_start_stdin_eof_watch`). Only
//! when the grace window elapses does `TerminateJobObject` escalate; the
//! crash path (shell death) still relies on `KILL_ON_JOB_CLOSE` alone. The
//! spawn/stop/escalation paths are exercised by the ownership integration
//! tests on the platforms they run on; the full desktop-app D3 acceptance
//! (real WebView + installer chain) remains a separate, lead-owned gate
//! (see `apps/desktop/README.md`).
//!
//! Unix path: the spawned helper gets `PR_SET_PDEATHSIG=SIGKILL` before its
//! first instruction, and the SHELL makes it its own process-group leader at
//! spawn time (`process_group(0)`) — before any helper code runs — so a
//! shell crash kills the helper immediately and `stop()` can signal the
//! entire tree via the process group from the earliest possible moment. (The
//! helper's own `setsid()` remains as a harmless guard for non-shell
//! launchers; the shell no longer depends on it.)

use std::process::{Child, Command};
use std::time::{Duration, Instant};

/// Raw Win32 job handle. HANDLE is a plain integer handle valid across
/// threads; the single owner is the `OwnedTree` that created and closes it.
#[cfg(windows)]
pub struct JobHandle(windows::Win32::Foundation::HANDLE);
#[cfg(windows)]
unsafe impl Send for JobHandle {}

/// #150: the kernel Job object is released exactly once on every path.
/// Before this Drop impl, the assign/resume/SetInformation failure paths
/// killed the still-suspended child but leaked the job handle per failed
/// spawn; with Drop the release is unconditional and the failure paths need
/// no manual cleanup. The handle is null-checked because `HANDLE::default()`
/// (null) and invalid sentinel values must never reach `CloseHandle`.
#[cfg(windows)]
impl Drop for JobHandle {
    fn drop(&mut self) {
        if !self.0.is_invalid() && self.0 != windows::Win32::Foundation::HANDLE::default() {
            unsafe {
                let _ = windows::Win32::Foundation::CloseHandle(self.0);
            }
        }
    }
}

pub enum TreeGuard {
    #[cfg(unix)]
    Unix,
    #[cfg(windows)]
    Windows { job: JobHandle },
}

#[derive(Debug)]
pub enum OwnershipError {
    Spawn(std::io::Error),
    JobAssignment(String),
    Resume(String),
    StopFailed(String),
}

pub struct OwnedTree {
    pub child: Child,
    pub guard: TreeGuard,
}

impl OwnedTree {
    /// Spawn the helper into shell ownership.
    ///
    /// # Windows ownership order (V02-54, mirrors `owned_processes.start_python`)
    ///
    /// `CREATE_SUSPENDED` spawn → create job (`KILL_ON_JOB_CLOSE`) → assign
    /// the still-suspended child → resume the initial thread. Any failure
    /// terminates the child while it is still suspended (it has executed
    /// nothing). The shell-side ownership journal is written by
    /// `RuntimeLayout::create` even earlier: before the process exists at
    /// all. Windows runtime behavior remains NOT RUN (Linux sandbox).
    pub fn spawn(mut command: Command) -> Result<OwnedTree, OwnershipError> {
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            // Die with the shell even on shell SIGKILL. Unsafe: runs between
            // fork and exec with only async-signal-safe calls.
            unsafe {
                command.pre_exec(|| {
                    if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0) != 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
            // The SHELL owns the process group: the child becomes its own
            // group leader before its first instruction, so `stop()`'s
            // `kill(-pid)` reaches the whole tree from the earliest possible
            // moment. The old arrangement relied on the helper calling
            // `setsid()` from Python — a stop landing during the interpreter
            // bootstrap (before that line ran) left descendants in the
            // SHELL's own group, where the group signal misses them and the
            // per-pid fallback orphans them. With the group claimed at spawn
            // time the window is gone; the helper's `setsid()` becomes a
            // no-op guard for non-shell launchers, not a load-bearing step.
            command.process_group(0);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            use windows::Win32::System::Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED};
            // Suspended creation is the point: the helper cannot run a single
            // instruction until it is inside the job. CREATE_NO_WINDOW keeps
            // the GUI shell from flashing a console for the Python helper.
            command.creation_flags(CREATE_SUSPENDED.0 | CREATE_NO_WINDOW.0);
        }
        let child = command.spawn().map_err(OwnershipError::Spawn)?;
        #[cfg(windows)]
        {
            let job = match create_kill_on_close_job() {
                Ok(job) => job,
                Err(error) => return Err(fail_suspended(child, error)),
            };
            if let Err(error) = assign_process(&job, &child) {
                return Err(fail_suspended(child, error));
            }
            if let Err(error) = resume_initial_thread(child.id()) {
                return Err(fail_suspended(child, error));
            }
            return Ok(OwnedTree {
                child,
                guard: TreeGuard::Windows { job },
            });
        }
        #[cfg(unix)]
        {
            Ok(OwnedTree {
                child,
                guard: TreeGuard::Unix,
            })
        }
    }

    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    /// Whether `pid` belongs to the process tree this shell owns.
    ///
    /// Windows: membership in the root Job Object (the helper was assigned
    /// before its first instruction and descendants inherit the job, so a
    /// launcher-style interpreter chain stays inside the kill boundary).
    /// Unix: only the direct child — `exec` semantics preserve the PID, so
    /// anything else announcing a foreign PID must be refused.
    pub fn contains_pid(&self, pid: u32) -> bool {
        if pid == self.pid() {
            return true;
        }
        #[cfg(windows)]
        match &self.guard {
            TreeGuard::Windows { job } => return job_contains_pid(job, pid),
        }
        #[allow(unreachable_code)]
        false
    }

    pub fn alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    /// Graceful stop with an escalation deadline; kills the whole tree.
    ///
    /// Cooperative phase first: the child's piped stdin is closed — the
    /// helper's documented EOF watcher turns that into a self-SIGTERM and
    /// unwinds uvicorn through the FastAPI lifespan shutdown instead of being
    /// cut mid-flight. On Unix the process group is also SIGTERMed directly
    /// so descendants that do not watch stdin still get the cooperative
    /// signal. A child that exits during `grace` reports its own exit code.
    ///
    /// Escalation after `grace` kills the tree unconditionally: Unix SIGKILL
    /// to the process group (no exit code), Windows `TerminateJobObject` on
    /// the root Job (job exit code 125). The crash path (shell death) is
    /// unchanged: the job handle's `KILL_ON_JOB_CLOSE` drop still kills
    /// everything without any cooperation.
    ///
    /// Reliance: this requires the piped stdin handle to still be open in
    /// [`OwnedTree::child`] — nothing may `take()` it between spawn and stop
    /// (the handshake only borrows it to send GO).
    pub fn stop(&mut self, grace: Duration) -> Result<Option<i32>, OwnershipError> {
        // A caller can arrive after the child was already reaped (native-host
        // polls `try_wait` and stops the tree when a self-exited helper is
        // observed). Signaling first would fire `signal_tree` at a freed pid
        // — possibly recycled into an unrelated process group — before the
        // grace loop ever reaches its own exit check, so the already-reaped
        // case returns the cached status up front. `try_wait` keeps
        // returning the exit status after the first reap.
        let already_reaped = match self.child.try_wait() {
            Ok(reaped) => reaped,
            Err(error) => return Err(OwnershipError::StopFailed(error.to_string())),
        };
        if let Some(status) = already_reaped {
            return Ok(status.code());
        }
        // Cooperative phase: dropping the piped stdin closes the pipe's write
        // end; the helper's EOF watcher is the graceful-shutdown trigger.
        // Idempotent — take() on an already-taken stdin is a no-op.
        drop(self.child.stdin.take());
        #[cfg(unix)]
        signal_tree(self.pid(), libc::SIGTERM);
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(status)) => {
                    // The direct child is gone, but group members may still
                    // be alive — a helper that crashed leaves its own
                    // children (plan B's node server) in the group, and a
                    // descendant can ignore the SIGTERM sent above. Escalate
                    // before returning: the shell owns the group until the
                    // last member exits, not merely until the child does.
                    // Group-only: the child pid is already reaped here, so
                    // the per-pid fallback could fire at a recycled pid.
                    #[cfg(unix)]
                    signal_group_only(self.pid(), libc::SIGKILL);
                    return Ok(status.code());
                }
                Ok(None) => std::thread::sleep(Duration::from_millis(20)),
                Err(error) => return Err(OwnershipError::StopFailed(error.to_string())),
            }
        }
        #[cfg(unix)]
        signal_tree(self.pid(), libc::SIGKILL);
        #[cfg(windows)]
        match &self.guard {
            TreeGuard::Windows { job } => unsafe {
                let _ = windows::Win32::System::JobObjects::TerminateJobObject(job.0, 125);
            },
        }
        match self.child.wait() {
            Ok(status) => Ok(status.code()),
            Err(error) => Err(OwnershipError::StopFailed(error.to_string())),
        }
    }
}

impl Drop for OwnedTree {
    fn drop(&mut self) {
        if self.alive() {
            let _ = self.stop(Duration::from_secs(3));
        } else {
            // The direct child already exited on its own, but its
            // descendants may live on inside the group this shell owns —
            // Unix has no KILL_ON_JOB_CLOSE analog, so nothing else would
            // ever signal them (the crash shape of plan B: the helper dies,
            // its node server lives on). Best-effort group SIGKILL: no
            // grace is possible during drop, and a process that ignored
            // the SIGTERM sent by an earlier stop() gets no second chance.
            // Group-only for the same recycled-pid reason as stop(): the
            // child pid was reaped the moment `alive()` observed the exit.
            #[cfg(unix)]
            signal_group_only(self.pid(), libc::SIGKILL);
        }
        // The Windows job handle now closes via `JobHandle::drop` (#150):
        // KILL_ON_JOB_CLOSE then kills anything that survived the graceful
        // stop, and the failure paths of `spawn` release their handle the
        // same way instead of leaking it.
    }
}

#[cfg(windows)]
fn fail_suspended(mut child: Child, error: OwnershipError) -> OwnershipError {
    // Fail closed: the child is still suspended and has executed nothing.
    let _ = child.kill();
    let _ = child.wait();
    error
}

#[cfg(windows)]
fn resume_initial_thread(child_pid: u32) -> Result<(), OwnershipError> {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, Thread32First, Thread32Next, TH32CS_SNAPTHREAD, THREADENTRY32,
    };
    use windows::Win32::System::Threading::{
        OpenThread, ResumeThread, THREAD_SUSPEND_RESUME,
    };

    unsafe {
        // A CREATE_SUSPENDED process has not executed a single instruction,
        // so it owns exactly one thread: its initial thread. Thread IDs are
        // not derivable from the PID, so enumerate a system snapshot.
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
            .map_err(|error| OwnershipError::Resume(error.to_string()))?;
        let mut entry = THREADENTRY32::default();
        entry.dwSize = std::mem::size_of::<THREADENTRY32>() as u32;
        let mut thread_id = None;
        if Thread32First(snapshot, &mut entry).is_ok() {
            loop {
                if entry.th32OwnerProcessID == child_pid {
                    thread_id = Some(entry.th32ThreadID);
                    break;
                }
                if Thread32Next(snapshot, &mut entry).is_err() {
                    break;
                }
            }
        }
        let _ = CloseHandle(snapshot);
        let thread_id = thread_id.ok_or_else(|| {
            OwnershipError::Resume(format!("no initial thread found for pid {child_pid}"))
        })?;
        let thread = OpenThread(THREAD_SUSPEND_RESUME, false, thread_id)
            .map_err(|error| OwnershipError::Resume(error.to_string()))?;
        let previous_suspend_count = ResumeThread(thread);
        let _ = CloseHandle(thread);
        if previous_suspend_count == u32::MAX {
            return Err(OwnershipError::Resume(
                "ResumeThread failed on the initial thread".to_string(),
            ));
        }
        Ok(())
    }
}

#[cfg(unix)]
fn signal_tree(pid: u32, sig: i32) {
    // `OwnedTree::spawn` made the child its own process-group leader before
    // its first instruction (`process_group(0)`), so the signal reaches the
    // whole tree — including during the helper's interpreter bootstrap, where
    // the helper's own late `setsid()` would not exist yet. The per-pid
    // fallback remains for a child that somehow lost the group.
    let group_signal = unsafe { libc::kill(-(pid as libc::pid_t), sig) };
    if group_signal != 0 {
        unsafe { libc::kill(pid as libc::pid_t, sig) };
    }
}

/// Group-only signaling for AFTER the direct child was reaped: the pid is
/// freed at that point, so `signal_tree`'s per-pid fallback could deliver a
/// signal to whatever process recycled it. An empty group fails with ESRCH
/// and is ignored — that is the harmless "nothing left to kill" case.
#[cfg(unix)]
fn signal_group_only(pid: u32, sig: i32) {
    unsafe { libc::kill(-(pid as libc::pid_t), sig) };
}

#[cfg(unix)]
pub fn pid_starttime(pid: u32) -> Option<u64> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let tail = stat.rsplit(')').next()?;
    let fields: Vec<&str> = tail.split_whitespace().collect();
    fields.get(19)?.parse().ok()
}

#[cfg(windows)]
fn create_kill_on_close_job() -> Result<JobHandle, OwnershipError> {
    use windows::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    unsafe {
        // Wrap the raw handle IMMEDIATELY after creation: a failure of
        // SetInformationJobObject below must not leak the job HANDLE. Before
        // this wrapper-first shape, the handle was only wrapped after BOTH
        // calls succeeded, so a SetInformationJobObject error returned
        // without ever closing it — one leaked kernel object per failed
        // spawn. With the wrapper owning it from here on, Drop closes it on
        // every path out of this function (the leak cannot be forced from a
        // test without injecting a failing setter; the release itself is
        // covered by the handle-count regression test below).
        let job = JobHandle(
            CreateJobObjectW(None, windows::core::PCWSTR::null())
                .map_err(|error| OwnershipError::JobAssignment(error.to_string()))?,
        );
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if let Err(error) = SetInformationJobObject(
            job.0,
            JobObjectExtendedLimitInformation,
            &limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) {
            return Err(OwnershipError::JobAssignment(error.to_string()));
        }
        Ok(job)
    }
}

#[cfg(windows)]
fn assign_process(job: &JobHandle, child: &Child) -> Result<(), OwnershipError> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::System::JobObjects::AssignProcessToJobObject;

    // The child handle is the process HANDLE kept by std::process::Child;
    // termination never needs a PID lookup.
    let handle = windows::Win32::Foundation::HANDLE(child.as_raw_handle());
    unsafe {
        AssignProcessToJobObject(job.0, handle)
            .map_err(|error| OwnershipError::JobAssignment(error.to_string()))?;
    }
    Ok(())
}

/// Whether `pid` is a member of the root Job Object (`IsProcessInJob`).
///
/// Used by the READY verification to accept launcher-style interpreter chains
/// (CPython 3.12 venv `python.exe` spawns the real interpreter as a child):
/// the announcer PID is then a grandchild, but it is still inside the kill
/// boundary this shell owns. A PID that left the job (or never joined it)
/// reports false. The query opens the process with
/// PROCESS_QUERY_LIMITED_INFORMATION, the least-privilege right sufficient
/// for `IsProcessInJob`; access denial is reported as "not ours" (fail
/// closed).
#[cfg(windows)]
fn job_contains_pid(job: &JobHandle, pid: u32) -> bool {
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::JobObjects::IsProcessInJob;
    use windows::Win32::System::Threading::{
        OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    unsafe {
        let process = match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
            Ok(handle) => handle,
            Err(_) => return false,
        };
        let mut in_job = windows::Win32::Foundation::BOOL::default();
        let verdict =
            IsProcessInJob(process, job.0, &mut in_job).is_ok() && in_job.as_bool();
        let _ = CloseHandle(process);
        verdict
    }
}

#[cfg(test)]
mod tests {
    // The Windows-only Job Object test calls `create_kill_on_close_job`
    // unqualified; on Linux this glob is genuinely unused, which is why a
    // Linux-only lint must not be allowed to delete it again.
    #[cfg(windows)]
    use super::*;

    /// #150 regression: creating and dropping JobHandles must not leak
    /// kernel objects — this is the release path the assign/resume failure
    /// branches now rely on via `JobHandle::drop`. Measured by the owning
    /// process handle count across a batch of create+drop cycles: a missing
    /// close leaks one handle per cycle (+64), while correct behavior stays
    /// at the baseline. Parallel test threads add bounded noise, so a small
    /// slack band is allowed.
    #[test]
    #[cfg(windows)]
    fn job_handle_drop_releases_the_kernel_object() {
        use windows::Win32::System::Threading::{GetCurrentProcess, GetProcessHandleCount};

        fn handle_count() -> u32 {
            let mut count = 0u32;
            unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut count) }
                .expect("GetProcessHandleCount");
            count
        }

        const CYCLES: u32 = 64;
        let before = handle_count();
        for _ in 0..CYCLES {
            drop(create_kill_on_close_job().expect("job object creation"));
        }
        let after = handle_count();
        assert!(
            after < before + CYCLES / 4,
            "job handles leaked across {CYCLES} create+drop cycles: \
             before={before} after={after}"
        );
    }
}
