"""Create source-only CranL archives. Never include local data or credentials."""

from pathlib import Path
import io
import tarfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {"node_modules", "build", ".svelte-kit", "__pycache__", "test-results", "playwright-report"}


def package(name, paths, dockerfile):
    output = ROOT / "artifacts/deployment"
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{name}.tar.gz"
    with tarfile.open(target, "w:gz") as archive:
        for relative in paths:
            path = ROOT / relative
            files = sorted(path.rglob("*")) if path.is_dir() else [path]
            for file in files:
                rel = file.relative_to(ROOT)
                if not file.is_file() or file.is_symlink():
                    continue
                if any(part.startswith(".") or part in EXCLUDED for part in rel.parts):
                    continue
                if file.suffix in {".db", ".wav", ".pem", ".key"}:
                    continue
                archive.add(file, arcname=str(rel), recursive=False)
        data = (ROOT / dockerfile).read_bytes()
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return target


if __name__ == "__main__":
    for name, paths, dockerfile in (
        (
            "web",
            [
                "apps/web/src",
                "apps/web/server.js",
                "apps/web/package.json",
                "apps/web/pnpm-lock.yaml",
                "apps/web/tsconfig.json",
                "apps/web/vite.config.ts",
                "packages/contracts",
            ],
            "infra/web.Dockerfile",
        ),
        ("api", ["apps/api", "migrations", "alembic.ini", "pyproject.toml", "uv.lock"], "infra/api.Dockerfile"),
    ):
        print(package(name, paths, dockerfile).relative_to(ROOT))
