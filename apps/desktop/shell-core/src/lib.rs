//! MangaFlow desktop shell core (V02-54 delivery; grown from the V02-53B
//! disposable PoC).
//!
//! Implements the frozen ADR startup protocol headlessly so it can be tested
//! without a GUI toolkit: runtime directory + owner token, atomic dynamic
//! port binding (shell side never probes), readiness handshake verification
//! (token + PID + journal), GO gating, loopback-only health polling, and
//! process-tree ownership (Windows root Job Object with `KILL_ON_JOB_CLOSE`,
//! Unix `PR_SET_PDEATHSIG` + process-group kill as the documented
//! Linux-equivalent evidence).

pub mod handshake;
pub mod ownership;
pub mod protocol;

pub use handshake::{spawn_helper, HelperConfig, SpawnedHelper};
pub use ownership::OwnershipError;
pub use protocol::{ReadyPayload, RuntimeLayout, VerifyError};
