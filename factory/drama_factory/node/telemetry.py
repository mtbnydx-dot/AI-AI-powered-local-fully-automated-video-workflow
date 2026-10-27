"""GPU and disk health reported with every node heartbeat (DCGM exporter covers Prometheus separately)."""
import shutil
import subprocess

QUERY = ("index,uuid,name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,"
         "ecc.errors.uncorrected.volatile.total")
FIELDS = ("index", "uuid", "name", "temperature_c", "utilization_pct", "memory_used_mb", "memory_total_mb",
          "power_w", "ecc_uncorrected")


def _number(value: str) -> int | float | None:
    """Numbers only: "[N/A]", "[Not Supported]" or any garbage becomes None, never a string."""
    value = value.strip()
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return None


def parse_gpus(csv_text: str) -> list[dict]:
    gpus = []
    for line in csv_text.strip().splitlines():
        values = [v.strip() for v in line.split(",")]
        if len(values) != len(FIELDS):
            continue
        row = dict(zip(FIELDS, values))
        gpus.append({key: (row[key] if key in ("uuid", "name") else _number(row[key])) for key in FIELDS})
    return gpus


def read_gpus(nvidia_smi: str = "nvidia-smi") -> list[dict]:
    try:
        out = subprocess.run([nvidia_smi, f"--query-gpu={QUERY}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return parse_gpus(out.stdout) if out.returncode == 0 else []


def disk_free(paths: list[str]) -> dict[str, float]:
    free = {}
    for path in paths:
        try:
            free[path] = round(shutil.disk_usage(path).free / 1e9, 1)
        except OSError:
            continue
    return free


def collect(disk_paths: list[str]) -> dict:
    return {"gpus": read_gpus(), "disks": disk_free(disk_paths)}
