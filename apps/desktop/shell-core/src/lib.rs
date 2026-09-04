//! MangaFlow desktop shell core (V02-54 delivery; grown from the V02-53B
//! disposable PoC).
//!
//! Implements the frozen ADR startup protocol headlessly so it can be tested
//! without a GUI toolkit: runtime directory + owner token, atomic dynamic
//! port binding (shell side never probes), readiness handshake verification
//! (token + PID + journal), GO gating, loopback-only health polling, and
//! process-tree ownership (Windows root Job Object with `KILL_ON_JOB_CLOSE`,
//! Unix `PR_SET_PDEATHSIG` + process-group kill as the documented
//! Linux-equivalent evidence). V02-54B adds the unified log layout with a
//! path-safe ZIP export, and the validated local file/directory pick surface
//! shared with the Tauri shell commands.

pub mod handshake;
pub mod logs;
pub mod ownership;
pub mod picker;
pub mod protocol;
pub mod ziparch;

pub use handshake::{spawn_helper, HelperConfig, SpawnedHelper};
pub use logs::{export_logs_zip, ExportError, ExportReport, RunLog};
pub use ownership::OwnershipError;
pub use picker::{
    read_registered_file, validate_picked_directory, validate_picked_file, PickError, PickKind,
    PickedDirectory, PickedFile, PickedRegistry, MAX_PICKED_FILE_BYTES,
};
pub use protocol::{ReadyPayload, RuntimeLayout, VerifyError};
