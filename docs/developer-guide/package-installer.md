# Package Installer Internals

The package manager installs reusable package repository components into runtime cores.
It does not modify source templates or manage Python dependencies.

## Repository Load

`PackageRepository.load()` reads one repository root:

```text
package-repository/repository.yaml
package-repository/packages/*.yaml
```

`load_package_repository_collection()` resolves configured repositories from
host config, then loads the ready repositories. It validates package ids,
options, component sources, conditions, and duplicate targets. Component sources
must stay inside their repository and cannot be symlinks.

Repository source definitions live in `<home>/config.yaml` under
`packages.repositories`. Git caches live under
`<home>/package-repositories/<alias>/`.

## Preview Flow

```text
resolve package
  -> normalize option answers
  -> apply when/config_when
  -> render config
  -> plan component targets
  -> validate pipeline inserts and target conflicts
  -> return PackageOperationPreview
```

Preview does not write files.

## Install Flow

Install copies components into the active runtime core, writes optional
`config.yaml` files, inserts bootstrap/input/output pipeline entries, installs
child cores when requested, and records ownership in:

```text
~/.demiurge/agents/<core>/packages.yaml
```

If install fails after copying components, copied non-reused components are
removed in reverse order.

## Uninstall Flow

Uninstall reads `packages.yaml`, removes owned components and pipeline entries,
and keeps reused shared targets until the final referencing package is removed.
Repository sources are not removed by uninstall.

## Boundary

Package recipes can install files and edit bootstrap/input/output pipelines.
Bootstrap pipelines are serial-only. Recipes do not run migrations, install host
dependencies, edit `uv.lock`, or create git commits. Manual dependencies are
warnings only.
