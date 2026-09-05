//! Process-tree ownership for the desktop shell.
//!
//! **Windows path (V02-54): suspended creation, assignment before the first
//! instruction, then resume — the `scripts/owned_processes.py` `start_python`
//! discipline.** The helper is created `CREATE_SUSPENDED`, assigned to the
//! root Job Object (`KILL_ON_JOB_CLOSE`) while it still has not executed a
//! single instruction, and only then its initial thread is resumed. The
//! spawn→assign race window of the V02-53B compile-only skeleton is gone:
//! whatever happens (graceful exit, shell crash, timeout), the job handle
//! closing kills the whole tree. The implementation compiles for
//! `x86_64-pc-windows-msvc`, but its runtime behavior is **NOT RUN** (this
//! sandbox is Linux): D3 must be re-verified on a real Windows machine before
//! this path is called production-proven (see `apps/desktop/README.md`).
//!
//! Unix path (runtime-verified in this sandbox): the spawned helper gets
//! `PR_SET_PDEATHSIG=SIGKILL` before its first instruction and puts itself
//! into its own session, so a shell crash kills the helper immediately and
//! the shell can signal the entire tree via the process group.

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

#[cfg(test)]
mod tests {
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
