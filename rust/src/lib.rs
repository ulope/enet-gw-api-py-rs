//! Python bindings for the `enet-client` / `enet-proto` crates.
//!
//! This crate exposes an asyncio-friendly wrapper around the Rust client for
//! the Jung/Gira "Funk Gateway IP" (eNet). It is primarily intended to be used
//! from a Home Assistant integration, so every method that performs network IO
//! returns a Python awaitable that is driven by a background Tokio runtime.

use std::sync::Arc;

use enet_client::dev::{DeviceKind, DeviceValue};
use enet_client::{ClickDuration, Device, EnetClient as RsEnetClient, EnetDevice, SetValue};
use eventuals::EventualReader;
use pyo3::exceptions::{PyRuntimeError, PyStopAsyncIteration, PyValueError};
use pyo3::prelude::*;
use pyo3_async_runtimes::tokio::future_into_py;
use tokio::sync::Mutex;

/// Format an error together with its full `source()` chain.
///
/// The `enet-client` error types use the same generic `Display` message for
/// every variant (e.g. "Failed to connect to gateway."), so the only way to see
/// *which* step actually failed is to walk the source chain.
fn error_chain(err: &dyn std::error::Error) -> String {
    let mut msg = err.to_string();
    let mut source = err.source();
    while let Some(cause) = source {
        msg.push_str(": ");
        msg.push_str(&cause.to_string());
        source = cause.source();
    }
    msg
}


/// Map the (non-exhaustive) [`DeviceKind`] enum to a stable lowercase string so
/// that Python callers never have to deal with Rust enum variants.
fn kind_str(kind: DeviceKind) -> &'static str {
    match kind {
        DeviceKind::Binary => "binary",
        DeviceKind::Dimmer => "dimmer",
        DeviceKind::Blinds => "blinds",
        // `DeviceKind` is `#[non_exhaustive]`.
        _ => "unknown",
    }
}

/// A single device exposed by the gateway.
///
/// Instances are cheap to clone and can be kept around for the lifetime of the
/// client. Subscribing yields a stream of [`PyDeviceValue`] updates.
#[pyclass(name = "Device", frozen, skip_from_py_object)]
#[derive(Clone)]
struct PyDevice {
    inner: Device,
}

#[pymethods]
impl PyDevice {
    /// The stable numeric identifier the gateway uses for this device.
    #[getter]
    fn number(&self) -> u32 {
        self.inner.number()
    }

    /// The human readable name configured in the eNet project.
    #[getter]
    fn name(&self) -> String {
        self.inner.name().to_string()
    }

    /// The device kind: `"binary"`, `"dimmer"` or `"blinds"`.
    #[getter]
    fn kind(&self) -> &'static str {
        kind_str(self.inner.kind())
    }

    /// Subscribe to state updates for this device.
    ///
    /// Returns an async iterator (`DeviceStream`) that yields a `DeviceValue`
    /// every time the gateway reports a change. The first value is delivered as
    /// soon as the gateway has reported the device's current state.
    fn subscribe(&self) -> PyDeviceStream {
        // `Device::subscribe` spawns background Tokio tasks (the `eventuals`
        // combinators), so it has to run inside the runtime's context.
        let _guard = pyo3_async_runtimes::tokio::get_runtime().enter();
        PyDeviceStream {
            reader: Arc::new(Mutex::new(self.inner.subscribe())),
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "Device(number={}, name={:?}, kind={:?})",
            self.inner.number(),
            self.inner.name(),
            kind_str(self.inner.kind()),
        )
    }
}

/// The value/state of a device at a point in time.
#[pyclass(name = "DeviceValue", frozen, skip_from_py_object)]
#[derive(Clone)]
struct PyDeviceValue {
    inner: DeviceValue,
}

#[pymethods]
impl PyDeviceValue {
    /// Whether the device is currently on.
    #[getter]
    fn is_on(&self) -> bool {
        self.inner.is_on()
    }

    /// The brightness in percent (`0..=100`) for dimmers, or `None` when the
    /// device is off/undefined or is not dimmable.
    #[getter]
    fn brightness(&self) -> Option<u8> {
        self.inner.brightness().map(|b| b.get())
    }

    /// A lowercase textual description of the state, e.g. `"on"`, `"off"`,
    /// `"undefined"`, `"all off"`, `"all on"` or a brightness like `"50"`.
    #[getter]
    fn state(&self) -> String {
        self.inner.to_string()
    }

    /// `True` when the state is undefined (not yet reported by the gateway).
    #[getter]
    fn is_undefined(&self) -> bool {
        matches!(self.inner, DeviceValue::Undefined)
    }

    fn __eq__(&self, other: &PyDeviceValue) -> bool {
        self.inner == other.inner
    }

    fn __repr__(&self) -> String {
        format!(
            "DeviceValue(state={:?}, is_on={}, brightness={:?})",
            self.inner.to_string(),
            self.inner.is_on(),
            self.inner.brightness().map(|b| b.get()),
        )
    }
}

/// An async iterator over device state updates.
///
/// Use it with `async for`:
///
/// ```python
/// async for value in device.subscribe():
///     print(value.is_on, value.brightness)
/// ```
#[pyclass(name = "DeviceStream")]
struct PyDeviceStream {
    reader: Arc<Mutex<EventualReader<DeviceValue>>>,
}

#[pymethods]
impl PyDeviceStream {
    fn __aiter__(slf: PyRef<'_, Self>) -> PyRef<'_, Self> {
        slf
    }

    fn __anext__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let reader = self.reader.clone();
        future_into_py(py, async move {
            let mut guard = reader.lock().await;
            match guard.next().await {
                Ok(value) => Ok(PyDeviceValue { inner: value }),
                // The underlying eventual was closed (e.g. the client was
                // dropped). Signal the end of the async iterator.
                Err(_) => Err(PyStopAsyncIteration::new_err("device stream closed")),
            }
        })
    }
}

/// A connected client for an eNet gateway.
///
/// Create one with [`EnetClient.connect`]. The client keeps two background
/// connections to the gateway alive (one for commands, one for events) for as
/// long as the object is alive.
#[pyclass(name = "EnetClient")]
struct PyEnetClient {
    inner: Arc<Mutex<RsEnetClient>>,
    devices: Vec<Device>,
}

impl PyEnetClient {
    /// Send a single [`SetValue`] to a device, mapping the various error types
    /// to a Python `RuntimeError`.
    fn set_value<'py>(
        &self,
        py: Python<'py>,
        number: u32,
        value: SetValue,
    ) -> PyResult<Bound<'py, PyAny>> {
        let inner = self.inner.clone();
        future_into_py(py, async move {
            let mut client = inner.lock().await;
            client
                .set_value(number, value)
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("failed to set value: {}", error_chain(&e))))
        })
    }
}

#[pymethods]
impl PyEnetClient {
    /// Connect to an eNet gateway.
    ///
    /// `host` is the hostname or IP address of the gateway, `port` defaults to
    /// the Funk Gateway IP's standard port. The returned awaitable resolves to a
    /// connected `EnetClient` once the project (device list) has been fetched.
    #[staticmethod]
    #[pyo3(signature = (host, port = 9050))]
    fn connect(py: Python<'_>, host: String, port: u16) -> PyResult<Bound<'_, PyAny>> {
        future_into_py(py, async move {
            let client = RsEnetClient::new((host, port))
                .await
                .map_err(|e| PyRuntimeError::new_err(format!("failed to connect: {}", error_chain(&e))))?;
            let devices = client.devices().to_vec();
            Ok(PyEnetClient {
                inner: Arc::new(Mutex::new(client)),
                devices,
            })
        })
    }

    /// The list of controllable devices reported by the gateway.
    #[getter]
    fn devices(&self) -> Vec<PyDevice> {
        self.devices
            .iter()
            .cloned()
            .map(|inner| PyDevice { inner })
            .collect()
    }

    /// Look up a single device by its number, or `None` if no such device
    /// exists.
    fn device(&self, number: u32) -> Option<PyDevice> {
        self.devices
            .iter()
            .find(|d| d.number() == number)
            .cloned()
            .map(|inner| PyDevice { inner })
    }

    /// Turn a device on.
    ///
    /// `long` sends a "long click" (used e.g. to start group/scene actions);
    /// the default is a normal short click.
    #[pyo3(signature = (number, long = false))]
    fn turn_on<'py>(
        &self,
        py: Python<'py>,
        number: u32,
        long: bool,
    ) -> PyResult<Bound<'py, PyAny>> {
        let duration = if long {
            ClickDuration::Long
        } else {
            ClickDuration::Short
        };
        self.set_value(py, number, SetValue::On(duration))
    }

    /// Turn a device off.
    #[pyo3(signature = (number, long = false))]
    fn turn_off<'py>(
        &self,
        py: Python<'py>,
        number: u32,
        long: bool,
    ) -> PyResult<Bound<'py, PyAny>> {
        let duration = if long {
            ClickDuration::Long
        } else {
            ClickDuration::Short
        };
        self.set_value(py, number, SetValue::Off(duration))
    }

    /// Set the brightness of a dimmer device to a percentage (`0..=100`).
    ///
    /// A value of `0` turns the device off.
    fn set_brightness<'py>(
        &self,
        py: Python<'py>,
        number: u32,
        brightness: u8,
    ) -> PyResult<Bound<'py, PyAny>> {
        if brightness > 100 {
            return Err(PyValueError::new_err("brightness must be in the range 0..=100"));
        }
        self.set_value(py, number, SetValue::Dimm(brightness))
    }

    /// Move a blinds (Jalousie) device to a position percentage (`0..=100`).
    ///
    /// The value is the raw gateway position: `0` and `100` are the two end
    /// stops. Read `DeviceValue.brightness` on a blinds device's subscription to
    /// get the current position back.
    fn set_blinds_position<'py>(
        &self,
        py: Python<'py>,
        number: u32,
        position: u8,
    ) -> PyResult<Bound<'py, PyAny>> {
        if position > 100 {
            return Err(PyValueError::new_err("position must be in the range 0..=100"));
        }
        self.set_value(py, number, SetValue::Blinds(position))
    }

    fn __repr__(&self) -> String {
        format!("EnetClient(devices={})", self.devices.len())
    }
}

static LOG_RESET: std::sync::OnceLock<pyo3_log::ResetHandle> = std::sync::OnceLock::new();

/// Install the bridge that forwards the underlying Rust client's `tracing` logs
/// into Python's standard `logging` module.
///
/// After this runs, the Rust logs appear under Python loggers named
/// `enet_gw_api_py_rs.enet-client.*` / `enet_gw_api_py_rs.enet-proto.*`, so they
/// honour whatever the application configures via `logging` (levels, handlers,
/// formatters). This is called automatically when the module is imported.
fn install_log_bridge(py: Python<'_>) {
    if LOG_RESET.get().is_some() {
        return;
    }
    let logger = match pyo3_log::Logger::new(py, pyo3_log::Caching::LoggersAndLevels) {
        Ok(logger) => logger,
        Err(_) => return,
    };
    let install = logger
        // Pass everything through to Python and let the Python loggers decide.
        .filter(log::LevelFilter::Trace)
        // Namespace the Rust logs under the package's logger.
        .set_prefix("enet_gw_api_py_rs")
        .install();
    if let Ok(handle) = install {
        let _ = LOG_RESET.set(handle);
    }
}

/// Clear the log bridge's cache of Python logger levels.
///
/// `pyo3-log` caches each logger's effective level the first time it sees it.
/// Call this if you reconfigure Python `logging` (e.g. change levels) after the
/// Rust side has already logged, so the new configuration takes effect.
#[pyfunction]
fn reset_logging_cache() {
    if let Some(handle) = LOG_RESET.get() {
        handle.reset();
    }
}

/// Python bindings for the Jung/Gira Funk Gateway IP (eNet) client.
#[pymodule]
fn enet_gw_api_py_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    install_log_bridge(m.py());
    m.add_class::<PyEnetClient>()?;
    m.add_class::<PyDevice>()?;
    m.add_class::<PyDeviceValue>()?;
    m.add_class::<PyDeviceStream>()?;
    m.add_function(wrap_pyfunction!(reset_logging_cache, m)?)?;
    Ok(())
}
