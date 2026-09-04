//! Process-tree ownership for the PoC shell.
//!
//! **Windows path is a COMPILE-ONLY SKELETON (V02-53B).** It compiles under
//! `cargo check --target x86_64-pc-windows-msvc` but its runtime behavior is
//! NOT RUN, and — unlike `scripts/owned_processes.py` — the helper is NOT
//! created suspended: `std::process::Command` cannot spawn suspended, so the
//! helper runs its first instructions before `AssignProcessToJobObject`, and
//! a shell crash in that window would leak the tree. This code must NOT be
//! mistaken for a production launcher: a production shell implements
//! `CreateProcessW(CREATE_SUSPENDED)` + assign + `ResumeThread` (see
//! `scripts/owned_processes.py` `start_python`) and re-verifies on Windows.
//!
//! Unix path (runtime-verified in this sandbox): the spawned helper gets
//! `PR_SET_PDEATHSIG=SIGKILL` before its first instruction and puts itself
//! into its own session, so a shell crash kills the helper immediately and
//! the shell can signal the entire tree via the process group.

use std::process::Child;
use std::time::{Duration, Instant};

/// Raw Win32 job handle. HANDLE is a plain integer handle valid across
/// threads; the single owner is the `OwnedTree` that created and closes it.
#[cfg(windows)]
pub struct JobHandle(windows::Win32::Foundation::HANDLE);
#[cfg(windows)]
unsafe impl Send for JobHandle {}

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
    StopFailed(String),
}

pub struct OwnedTree {
    pub child: Child,
    pub guard: TreeGuard,
}

impl OwnedTree {
    /// Spawn the helper into shell ownership.
    ///
    /// # Windows skeleton caveat (V02-53B)
    ///
    /// On Windows this assigns the ALREADY-RUNNING child to the job, leaving
    /// a spawn→assign race window (compile-only skeleton; see module docs).
    /// Do not ship this path as a production launcher.
    pub fn spawn(mut command: std::process::Command) -> Result<OwnedTree, OwnershipError> {
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
        }
        let child = command.spawn().map_err(OwnershipError::Spawn)?;
        #[cfg(windows)]
        {
            // SKELETON: assign-after-spawn, NOT the suspended first-instruction
            // assignment owned_processes.py uses. Windows runtime NOT RUN.
            let guard = TreeGuard::Windows {
                job: create_kill_on_close_job()?,
            };
            assign_to_job(&guard, &child)?;
            return Ok(OwnedTree { child, guard });
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

    pub fn alive(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(None))
    }

    /// Graceful stop with an escalation deadline; kills the whole tree.
    pub fn stop(&mut self, grace: Duration) -> Result<Option<i32>, OwnershipError> {
        #[cfg(windows)]
        match &self.guard {
            TreeGuard::Windows { job } => unsafe {
                let _ = windows::Win32::System::JobObjects::TerminateJobObject(job.0, 125);
            },
        }
        #[cfg(unix)]
        signal_tree(self.pid(), libc::SIGTERM);
        let deadline = Instant::now() + grace;
        while Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(status)) => return Ok(status.code()),
                Ok(None) => std::thread::sleep(Duration::from_millis(20)),
                Err(error) => return Err(OwnershipError::StopFailed(error.to_string())),
            }
        }
        #[cfg(unix)]
        signal_tree(self.pid(), libc::SIGKILL);
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
        }
        #[cfg(windows)]
        match &self.guard {
            TreeGuard::Windows { job } => unsafe {
                let _ = windows::Win32::Foundation::CloseHandle(job.0);
            },
        }
    }
}

#[cfg(unix)]
fn signal_tree(pid: u32, sig: i32) {
    // The helper calls setsid(), so its process group id equals its pid and
    // the signal reaches every descendant that did not break away.
    let group_signal = unsafe { libc::kill(-(pid as libc::pid_t), sig) };
    if group_signal != 0 {
        unsafe { libc::kill(pid as libc::pid_t, sig) };
    }
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
        let job = CreateJobObjectW(None, windows::core::PCWSTR::null())
            .map_err(|error| OwnershipError::JobAssignment(error.to_string()))?;
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
        .map_err(|error| OwnershipError::JobAssignment(error.to_string()))?;
        Ok(JobHandle(job))
    }
}

#[cfg(windows)]
fn assign_to_job(guard: &TreeGuard, child: &Child) -> Result<(), OwnershipError> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::System::JobObjects::AssignProcessToJobObject;

    // Single-variant tree guard on the Windows target: the job handle is the
    // root Job Object created with KILL_ON_JOB_CLOSE.
    let TreeGuard::Windows { job } = guard;
    let handle = windows::Win32::Foundation::HANDLE(child.as_raw_handle());
    unsafe {
        AssignProcessToJobObject(job.0, handle)
            .map_err(|error| OwnershipError::JobAssignment(error.to_string()))?;
    }
    Ok(())
}
