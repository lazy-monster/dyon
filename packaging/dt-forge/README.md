# dt-forge → dyon

**This project has been renamed to [`dyon`](https://pypi.org/project/dyon/).**

`dt-forge` is now a thin compatibility package. Installing it pulls in `dyon` and
registers a shim so existing `import dt_forge` code keeps working — every
`dt_forge` / `dt_forge.*` import transparently resolves to the matching `dyon`
module, with a one-time `DeprecationWarning`.

## Migrate

```bash
pip install dyon
```

Then replace `dt_forge` with `dyon` in your imports
(`import dyon`, `from dyon.core.config import TwinConfig`) and `dtforge` with
`dyon` on the command line. The compatibility shim will be removed in a future
major release.
