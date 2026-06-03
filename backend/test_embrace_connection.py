"""
Teste de conexão e leitura dos mapas EMBRACE/INPE.

Uso:
    python test_embrace_connection.py

Requer:
    pip install httpx numpy
"""

import re
import sys
from datetime import datetime, timedelta, timezone

try:
    import httpx
    import numpy as np
except ImportError as e:
    print(f"ERRO: dependência não instalada — {e}")
    print("      Execute: pip install httpx numpy")
    sys.exit(1)

BASE_URL  = "https://embracedata.inpe.br/scintillation/maps"
TEST_LAT, TEST_LON = -23.5, -46.6
LON_MIN, LON_MAX = -140.0, -20.0
LAT_MIN, LAT_MAX = -60.0, 50.0


def dir_url(param: str, year: int, doy: int) -> str:
    return f"{BASE_URL}/{param}/{year}/{doy:03d}/"


def file_url(param: str, year: int, doy: int, hhmm: str) -> str:
    date_str = (datetime(year, 1, 1) + timedelta(days=doy - 1)).strftime("%Y%m%d")
    prefix = "S4_MAP" if param == "s4" else "SIGMAPHI_MAP"
    return f"{BASE_URL}/{param}/{year}/{doy:03d}/{prefix}_{date_str}_{hhmm}.txt"


def latest_hhmm_in_dir(html: str, param: str) -> str | None:
    """Extract the latest HHMM from an Apache directory listing."""
    prefix = "S4_MAP" if param == "s4" else "SIGMAPHI_MAP"
    times = re.findall(rf'{prefix}_\d{{8}}_(\d{{4}})\.txt', html)
    return max(times) if times else None


def parse_matrix(text: str) -> np.ndarray:
    rows = [[float(v) for v in line.split(";")]
            for line in text.strip().splitlines() if line.strip()]
    arr = np.array(rows, dtype=np.float32)
    arr[arr < 0] = np.nan
    return arr


def interpolate(matrix: np.ndarray, lat: float, lon: float):
    nrows, ncols = matrix.shape
    col_f = (lon - LON_MIN) / (LON_MAX - LON_MIN) * (ncols - 1)
    row_f = (lat - LAT_MIN) / (LAT_MAX - LAT_MIN) * (nrows - 1)
    if not (0 <= col_f <= ncols - 1 and 0 <= row_f <= nrows - 1):
        return None
    c0 = min(int(col_f), ncols - 2)
    r0 = min(int(row_f), nrows - 2)
    dc, dr = col_f - c0, row_f - r0
    v = (matrix[r0, c0]*(1-dc)*(1-dr) + matrix[r0, c0+1]*dc*(1-dr)
       + matrix[r0+1, c0]*(1-dc)*dr   + matrix[r0+1, c0+1]*dc*dr)
    return None if np.isnan(v) else float(v)


def find_latest(client: httpx.Client, param: str) -> tuple[str | None, str | None, int | None]:
    """
    Search today and yesterday for the most recent available file.
    Returns (file_url, hhmm, doy) or (None, None, None).
    """
    now = datetime.now(timezone.utc)
    for delta in range(2):  # today, then yesterday
        day = now - timedelta(days=delta)
        year = day.year
        doy  = day.timetuple().tm_yday
        url  = dir_url(param, year, doy)
        try:
            r = client.get(url, timeout=15.0)
            if r.status_code != 200:
                continue
            hhmm = latest_hhmm_in_dir(r.text, param)
            if hhmm:
                return file_url(param, year, doy, hhmm), hhmm, doy
        except Exception as exc:
            print(f"   Erro ao listar diretório {url}: {exc}")
    return None, None, None


# ── Main ──────────────────────────────────────────────────────────────────────

print("=" * 60)
print("  Teste de conexão EMBRACE/INPE")
print("=" * 60)
print(f"  Servidor  : {BASE_URL}")
print(f"  Local teste: lat={TEST_LAT}, lon={TEST_LON} (São Paulo)")
print()

with httpx.Client(timeout=20.0) as client:

    # 1. Acessibilidade
    print("1. Verificando acesso ao servidor…")
    try:
        r = client.get(f"{BASE_URL}/s4/", timeout=10.0)
        print(f"   Status: {r.status_code} {'✓' if r.status_code == 200 else '✗'}")
    except Exception as exc:
        print(f"   ERRO de rede: {exc}")
        sys.exit(1)

    # 2. Arquivo S4 mais recente
    print()
    print("2. Localizando mapa S4 mais recente via listagem de diretório…")
    s4_url, s4_hhmm, s4_doy = find_latest(client, "s4")
    if not s4_url:
        print("   ✗ Nenhum mapa S4 encontrado.")
        sys.exit(1)
    print(f"   Arquivo mais recente: {s4_url.split('/')[-1]}")

    r = client.get(s4_url, timeout=20.0)
    r.raise_for_status()
    s4_matrix = parse_matrix(r.text)
    nrows, ncols = s4_matrix.shape
    valid = int(np.sum(~np.isnan(s4_matrix)))
    print(f"   Grid: {nrows} linhas × {ncols} colunas  |  dados válidos: {valid}/{nrows*ncols} ({100*valid/(nrows*ncols):.1f}%)")

    s4_val = interpolate(s4_matrix, TEST_LAT, TEST_LON)
    print(f"   S4 em São Paulo: {s4_val:.4f}" if s4_val is not None else "   S4: sem dado no pixel (pode ser normal à noite)")

    # 3. Arquivo sigma_phi mais recente
    print()
    print("3. Localizando mapa sigma_phi mais recente…")
    phi_url, phi_hhmm, phi_doy = find_latest(client, "sigma_phi")
    if phi_url:
        print(f"   Arquivo mais recente: {phi_url.split('/')[-1]}")
        r = client.get(phi_url, timeout=20.0)
        r.raise_for_status()
        phi_matrix = parse_matrix(r.text)
        phi_val = interpolate(phi_matrix, TEST_LAT, TEST_LON)
        print(f"   sigma_phi em São Paulo: {phi_val:.4f} rad" if phi_val is not None else "   sigma_phi: sem dado no pixel")
    else:
        print("   ✗ Nenhum mapa sigma_phi encontrado.")

print()
print("=" * 60)
print("  ✓ Conexão EMBRACE bem-sucedida!")
print("  Nota: dados têm atraso de ~6-9 h (processamento normal).")
print("=" * 60)
