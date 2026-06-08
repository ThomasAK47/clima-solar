"""
backtest_score.py — Backtest do motor de risco do Clima Solar
=============================================================
Baixa dados históricos de Kp (GFZ Potsdam), Dst (Kyoto WDC) e F10.7
(NOAA Solar Cycle JSON), roda o risk_engine para cada hora do período
e gera um relatório CSV + resumo no terminal.

Uso:
    python backend/scripts/backtest_score.py              # padrão: maio 2024
    python backend/scripts/backtest_score.py --start 2024-05-10 --end 2024-05-13
    python backend/scripts/backtest_score.py --event tempestade_maio_2024
    python backend/scripts/backtest_score.py --all-events
"""

import argparse
import csv
import io
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Garante UTF-8 no terminal Windows (cp1252 não suporta ═, █, etc.)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() not in ("utf-8", "utf8"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import httpx

# ── sys.path: adiciona raiz do backend ───────────────────────────────────────
SCRIPT_DIR  = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.core.risk_engine import _dst_score, _f107_score, _kp_score, _WEIGHTS

# ── Eventos pré-definidos ─────────────────────────────────────────────────────
EVENTS = {
    "tempestade_maio_2024": {
        "label":    "Tempestade Geomagnética — Maio 2024",
        "start":    "2024-05-10",
        "end":      "2024-05-13",
        "expected": "Score ALTO, Kp ≥ 9, Dst ~ −400 nT",
    },
    "tempestade_marco_2023": {
        "label":    "Tempestade Geomagnética — Março 2023",
        "start":    "2023-03-23",
        "end":      "2023-03-25",
        "expected": "Score ALTO, Kp ≥ 7",
    },
    "periodo_calmo": {
        "label":    "Período Calmo — Janeiro 2024",
        "start":    "2024-01-01",
        "end":      "2024-01-08",
        "expected": "Score BAIXO",
    },
}

DEFAULT_START = "2024-05-01"
DEFAULT_END   = "2024-06-01"

TIMEOUT = 30.0

# ── URLs ──────────────────────────────────────────────────────────────────────
# Kp histórico: GFZ Helmholtz Centre (dados desde 1932, 3h em 3h)
_GFZ_FULL     = "https://www-app3.gfz-potsdam.de/kp_index/Kp_ap_since_1932.txt"
_GFZ_NOWCAST  = "https://www-app3.gfz-potsdam.de/kp_index/Kp_ap_nowcast.txt"
_NOAA_KP      = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"

# Dst histórico: Kyoto WDC (HTML com tabela em <pre>), múltiplas categorias
_KYOTO_DST    = [
    "https://wdc.kugi.kyoto-u.ac.jp/dst_final/{ym}/index.html",
    "https://wdc.kugi.kyoto-u.ac.jp/dst_provisional/{ym}/index.html",
    "https://wdc.kugi.kyoto-u.ac.jp/dst_realtime/{ym}/index.html",
]
_NOAA_DST     = "https://services.swpc.noaa.gov/products/kyoto-dst.json"

# F10.7: ciclo solar NOAA (mensal, de 1749 ao presente)
_SOLAR_CYCLE  = "https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json"
_NOAA_F107    = "https://services.swpc.noaa.gov/products/summary/10cm-flux.json"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_ts(raw: str) -> datetime:
    ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _nearest(series: dict[datetime, float], ts: datetime, max_gap_h: int = 4) -> Optional[float]:
    """Retorna valor mais próximo de ts dentro de max_gap_h horas."""
    if not series:
        return None
    best = min(series.keys(), key=lambda t: abs((t - ts).total_seconds()))
    if abs((best - ts).total_seconds()) > max_gap_h * 3600:
        return None
    return series[best]


def _f107_for(series: dict[datetime, float], ts: datetime) -> Optional[float]:
    """F10.7 é diário/mensal — busca o dia ou mês correspondente."""
    day_key = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if day_key in series:
        return series[day_key]
    return _nearest(series, day_key, max_gap_h=48)


# ─────────────────────────────────────────────────────────────────────────────
# Coleta de Kp  (GFZ Potsdam → fallback NOAA)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_gfz_kp(text: str, start: datetime, end: datetime) -> dict[datetime, float]:
    """
    Parseia o arquivo texto GFZ Potsdam.
    Formato por linha (espaços simples):
      YYYY MM DD  HH.H  midpoint  MJD_start  MJD_end  Kp  ap  flag
    índices:  0    1    2     3       4          5       6    7   8   9
    """
    result: dict[datetime, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 9:
            continue
        try:
            year  = int(parts[0])
            month = int(parts[1])
            day   = int(parts[2])
            hour  = int(float(parts[3]))   # start hour of 3h window (0,3,6,...,21)
            kp    = float(parts[7])
            ts    = datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc)
            if start <= ts <= end:
                result[ts] = kp
        except (ValueError, IndexError):
            continue
    return result


def fetch_kp_series(client: httpx.Client, start: datetime, end: datetime) -> dict[datetime, float]:
    """Baixa série de Kp para o período, priorizando GFZ (cobertura desde 1932)."""
    print("  [Kp]  GFZ Helmholtz Centre / NOAA…")

    # Decide qual arquivo GFZ usar: nowcast (~30 dias mais recente) ou full
    now = datetime.now(timezone.utc)
    use_nowcast = (now - start).days <= 45

    for url, desc in [
        (_GFZ_NOWCAST if use_nowcast else _GFZ_FULL, "GFZ nowcast" if use_nowcast else "GFZ full"),
        (_GFZ_FULL if use_nowcast else _GFZ_NOWCAST, "GFZ full" if use_nowcast else "GFZ nowcast"),
    ]:
        try:
            resp = client.get(url, timeout=TIMEOUT)
            if resp.status_code != 200:
                continue
            series = _parse_gfz_kp(resp.text, start, end)
            if series:
                print(f"         {len(series)} registros Kp ({desc})")
                return series
        except Exception as exc:
            print(f"         {desc} falhou: {exc}")

    # Fallback: NOAA (só últimos ~30 dias)
    try:
        resp = client.get(_NOAA_KP, timeout=TIMEOUT)
        resp.raise_for_status()
        series: dict[datetime, float] = {}
        for row in resp.json():
            if isinstance(row, dict) and row.get("Kp") is not None:
                ts = _parse_ts(row["time_tag"])
                if start <= ts <= end:
                    series[ts] = float(row["Kp"])
        if series:
            print(f"         {len(series)} registros Kp (NOAA fallback)")
            return series
    except Exception as exc:
        print(f"         NOAA fallback falhou: {exc}")

    print("  ⚠️   Nenhum dado Kp encontrado — score usará fallback=0.5")
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Coleta de Dst  (Kyoto WDC HTML → fallback NOAA)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_kyoto_dst_html(html: str, year: int, month: int) -> dict[datetime, float]:
    """
    Parseia a tabela <pre> do Kyoto WDC.
    Linhas de dados: primeira coluna = dia (1-31), seguido de 24 valores horários.
    Coluna 1 → 00:00 UTC, coluna 24 → 23:00 UTC.
    Valores 9999 / 99999 = indisponíveis.
    Números negativos podem estar colados (ex: -277-339) → regex trata corretamente.
    """
    result: dict[datetime, float] = {}

    # Extrai apenas o bloco <pre>
    pre_match = re.search(r'<pre[^>]*>(.*?)</pre>', html, re.DOTALL | re.IGNORECASE)
    if not pre_match:
        return result
    block = pre_match.group(1)

    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Ignora linhas de cabeçalho (não começam com número)
        if not re.match(r'^\s*\d+', stripped):
            continue
        # Extrai todos os inteiros da linha (incluindo negativos colados)
        nums = re.findall(r'-?\d+', stripped)
        if len(nums) < 25:          # dia + 24 horas
            continue
        try:
            day = int(nums[0])
            if not (1 <= day <= 31):
                continue
            for col_idx in range(24):
                val = int(nums[col_idx + 1])
                if abs(val) >= 9999:
                    continue
                ts = datetime(year, month, day, col_idx, 0, 0, tzinfo=timezone.utc)
                result[ts] = float(val)
        except (ValueError, IndexError):
            continue

    return result


def fetch_dst_series(client: httpx.Client, start: datetime, end: datetime) -> dict[datetime, float]:
    """Baixa série de Dst para o período, iterando meses e fontes disponíveis."""
    print("  [Dst] Kyoto WDC / NOAA…")
    series: dict[datetime, float] = {}

    # Itera sobre os meses do período
    months: list[tuple[int, int]] = []
    cur = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while cur <= end:
        months.append((cur.year, cur.month))
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        cur = nxt

    for year, month in months:
        ym = f"{year}{month:02d}"
        found = False

        for url_tpl in _KYOTO_DST:
            url = url_tpl.format(ym=ym)
            try:
                resp = client.get(url, timeout=TIMEOUT)
                if resp.status_code != 200:
                    continue
                parsed = _parse_kyoto_dst_html(resp.text, year, month)
                if not parsed:
                    continue
                in_range = {ts: v for ts, v in parsed.items() if start <= ts <= end}
                series.update(in_range)
                print(f"         Kyoto {ym}: {len(in_range)} registros Dst")
                found = True
                break
            except Exception as exc:
                continue  # tenta próxima URL

        if not found:
            print(f"         Kyoto {ym}: sem dados — tentando NOAA espelho")

    # Complementa com NOAA (últimos ~30 dias) se ainda faltar
    if len(series) < 10:
        try:
            resp = client.get(_NOAA_DST, timeout=TIMEOUT)
            resp.raise_for_status()
            for row in resp.json():
                if isinstance(row, dict) and row.get("dst") is not None:
                    ts = _parse_ts(row["time_tag"])
                    if start <= ts <= end:
                        series[ts] = float(row["dst"])
            print(f"         NOAA espelho: {len(series)} registros Dst total")
        except Exception as exc:
            print(f"         NOAA espelho falhou: {exc}")

    if not series:
        print("  ⚠️   Nenhum dado Dst encontrado — score usará fallback=0.5")

    print(f"         Total Dst no período: {len(series)} registros")
    return series


# ─────────────────────────────────────────────────────────────────────────────
# Coleta de F10.7  (NOAA Solar Cycle JSON — mensal desde 1749)
# ─────────────────────────────────────────────────────────────────────────────

def fetch_f107_series(client: httpx.Client, start: datetime, end: datetime) -> dict[datetime, float]:
    """Baixa F10.7 mensal do NOAA Solar Cycle JSON e propaga diariamente."""
    print("  [F10.7] NOAA Solar Cycle…")
    series: dict[datetime, float] = {}

    try:
        resp = client.get(_SOLAR_CYCLE, timeout=TIMEOUT)
        resp.raise_for_status()
        for entry in resp.json():
            try:
                tag = entry.get("time-tag", "")          # "2024-05"
                f   = entry.get("f10.7") or entry.get("f107")
                if not f or not tag:
                    continue
                y, m = int(tag[:4]), int(tag[5:7])
                f_val = float(f)
                # Propaga para cada dia do mês
                day_ts = datetime(y, m, 1, tzinfo=timezone.utc)
                while day_ts.month == m:
                    if start.replace(hour=0,minute=0,second=0,microsecond=0) <= day_ts <= end:
                        series[day_ts] = f_val
                    day_ts += timedelta(days=1)
            except (ValueError, KeyError, TypeError):
                continue
        print(f"         {len(series)} registros F10.7 (dias propagados de médias mensais)")
    except Exception as exc:
        print(f"         Solar Cycle JSON falhou: {exc}")

    # Fallback: valor atual propagado para todo o período
    if not series:
        try:
            resp2 = client.get(_NOAA_F107, timeout=TIMEOUT)
            resp2.raise_for_status()
            data = resp2.json()
            flux = None
            if isinstance(data, dict):
                flux = float(data.get("Flux") or data.get("flux") or 0)
            elif isinstance(data, list) and data:
                last = data[-1]
                flux = float(last.get("flux") or last.get("Flux") or 0) if isinstance(last, dict) else None
            if flux:
                cur = start.replace(hour=0, minute=0, second=0, microsecond=0)
                while cur <= end:
                    series[cur] = flux
                    cur += timedelta(days=1)
                print(f"         F10.7 atual ({flux:.0f} sfu) propagado (fallback)")
        except Exception as exc2:
            print(f"         F10.7 fallback falhou: {exc2}")

    return series


# ─────────────────────────────────────────────────────────────────────────────
# Motor de risco inline
# ─────────────────────────────────────────────────────────────────────────────

def _compute_score(kp: Optional[float], dst: Optional[float],
                   f107: Optional[float]) -> tuple[float, str]:
    """Réplica de point_score() para dados históricos sem EMBRACE."""
    kp_s   = _kp_score(kp)     if kp   is not None else 0.5
    dst_s  = _dst_score(dst)   if dst  is not None else 0.5
    f107_s = _f107_score(f107) if f107 is not None else 0.5

    available  = {"kp": kp_s, "dst": dst_s, "f107": f107_s}
    total_w    = sum(_WEIGHTS[k] for k in available)
    scale      = 1.0 / total_w if total_w > 0 else 1.0
    score      = round(
        min(max(sum(available[k] * _WEIGHTS[k] for k in available) * scale, 0.0), 1.0), 4
    )
    level = "BAIXO" if score < 0.3 else "MÉDIO" if score < 0.6 else "ALTO"
    return score, level


# ─────────────────────────────────────────────────────────────────────────────
# Backtest principal
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(start: datetime, end: datetime, label: str = "") -> list[dict]:
    print(f"\n{'═'*60}")
    print(f"  {label or 'Backtest'}")
    print(f"  Período: {start.date()} → {(end - timedelta(seconds=1)).date()}")
    print(f"{'═'*60}")

    with httpx.Client(follow_redirects=True, timeout=TIMEOUT) as client:
        kp_s   = fetch_kp_series(client, start, end)
        dst_s  = fetch_dst_series(client, start, end)
        f107_s = fetch_f107_series(client, start, end)

    # Gera timestamps hora a hora
    results: list[dict] = []
    ts = start
    while ts < end:
        kp   = _nearest(kp_s,   ts, max_gap_h=3)
        dst  = _nearest(dst_s,  ts, max_gap_h=2)
        f107 = _f107_for(f107_s, ts)
        score, level = _compute_score(kp, dst, f107)
        results.append({
            "timestamp": ts.isoformat(),
            "kp":        round(kp,   2) if kp   is not None else None,
            "dst_nt":    round(dst,  1) if dst  is not None else None,
            "f107_sfu":  round(f107, 1) if f107 is not None else None,
            "score":     score,
            "nivel":     level,
        })
        ts += timedelta(hours=1)

    # Cobertura de dados
    n = len(results)
    n_kp   = sum(1 for r in results if r["kp"]      is not None)
    n_dst  = sum(1 for r in results if r["dst_nt"]  is not None)
    n_f107 = sum(1 for r in results if r["f107_sfu"] is not None)
    print(f"\n  Cobertura de dados ({n} horas):")
    print(f"    Kp     {n_kp:4d}/{n} ({n_kp/n*100:5.1f}%)")
    print(f"    Dst    {n_dst:4d}/{n} ({n_dst/n*100:5.1f}%)")
    print(f"    F10.7  {n_f107:4d}/{n} ({n_f107/n*100:5.1f}%)")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Relatório
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(val, digits=2, suffix="") -> str:
    return f"{val:.{digits}f}{suffix}" if val is not None else "—"


def print_summary(results: list[dict], label: str = "", expected: str = "") -> None:
    if not results:
        print("  (sem resultados)"); return

    counts = {"BAIXO": 0, "MÉDIO": 0, "ALTO": 0}
    for r in results:
        counts[r["nivel"]] = counts.get(r["nivel"], 0) + 1

    max_row  = max(results, key=lambda r: r["score"])
    total    = len(results)

    print(f"\n  {'─'*56}")
    print(f"  Período : {results[0]['timestamp'][:10]} → {results[-1]['timestamp'][:10]}")
    print(f"  Horas   : {total}")
    if expected:
        print(f"  Esperado: {expected}")

    print()
    print(f"  Score máximo : {max_row['score']:.4f}  em  {max_row['timestamp'][:16]} UTC")
    print(f"    Kp={_fmt(max_row['kp'],1)}  "
          f"Dst={_fmt(max_row['dst_nt'],0,' nT')}  "
          f"F10.7={_fmt(max_row['f107_sfu'],0,' sfu')}")

    print()
    print(f"  Distribuição de níveis:")
    for nivel, sym in [("BAIXO","🟢"), ("MÉDIO","🟡"), ("ALTO","🔴")]:
        n   = counts.get(nivel, 0)
        pct = n / total * 100 if total else 0
        bar = "█" * int(pct / 2)
        print(f"    {sym} {nivel:6s}  {n:4d}h  ({pct:5.1f}%)  {bar}")

    print()
    print(f"  10 piores momentos (score decrescente):")
    hdr = f"  {'Timestamp':<20}  {'Score':>6}  {'Nivel':>6}  {'Kp':>5}  {'Dst (nT)':>8}  {'F10.7':>7}"
    sep = f"  {'─'*20}  {'─'*6}  {'─'*6}  {'─'*5}  {'─'*8}  {'─'*7}"
    print(hdr); print(sep)
    for r in sorted(results, key=lambda r: r["score"], reverse=True)[:10]:
        print(
            f"  {r['timestamp'][:16]:<20}  {r['score']:6.4f}  {r['nivel']:>6}  "
            f"{_fmt(r['kp'],1):>5}  {_fmt(r['dst_nt'],0):>5} nT  "
            f"{_fmt(r['f107_sfu'],0):>5} sfu"
        )


def save_csv(results: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp","kp","dst_nt","f107_sfu","score","nivel"])
        w.writeheader(); w.writerows(results)
    print(f"\n  CSV salvo em: {path}  ({len(results)} linhas)")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest do motor de risco do Clima Solar."
    )
    parser.add_argument("--start",      default=DEFAULT_START)
    parser.add_argument("--end",        default=DEFAULT_END)
    parser.add_argument("--event",      choices=list(EVENTS))
    parser.add_argument("--all-events", action="store_true")
    parser.add_argument("--csv",        default="")
    args = parser.parse_args()

    if args.all_events:
        all_res: dict[str, list[dict]] = {}
        for key, ev in EVENTS.items():
            start = _parse_date(ev["start"])
            end   = _parse_date(ev["end"]) + timedelta(days=1)
            res   = run_backtest(start, end, label=ev["label"])
            print_summary(res, label=ev["label"], expected=ev["expected"])
            csv_path = SCRIPT_DIR / f"backtest_{key}.csv"
            save_csv(res, csv_path)
            all_res[key] = res

        print(f"\n{'═'*60}")
        print("  COMPARATIVO DOS EVENTOS")
        print(f"{'═'*60}")
        hdr = f"  {'Evento':<44}  {'ScoreMax':>8}  {'%ALTO':>6}  {'%MED':>6}  {'%BAI':>6}"
        print(hdr)
        print(f"  {'─'*44}  {'─'*8}  {'─'*6}  {'─'*6}  {'─'*6}")
        for key, ev in EVENTS.items():
            res = all_res.get(key, [])
            if not res: continue
            total  = len(res)
            max_s  = max(r["score"] for r in res)
            pct_a  = sum(1 for r in res if r["nivel"]=="ALTO")  / total * 100
            pct_m  = sum(1 for r in res if r["nivel"]=="MÉDIO") / total * 100
            pct_b  = sum(1 for r in res if r["nivel"]=="BAIXO") / total * 100
            print(f"  {ev['label']:<44}  {max_s:8.4f}  {pct_a:5.1f}%  {pct_m:5.1f}%  {pct_b:5.1f}%")
        return

    if args.event:
        ev    = EVENTS[args.event]
        start = _parse_date(ev["start"])
        end   = _parse_date(ev["end"]) + timedelta(days=1)
        label, expected = ev["label"], ev["expected"]
    else:
        start    = _parse_date(args.start)
        end      = _parse_date(args.end)
        label    = f"Backtest {args.start} → {args.end}"
        expected = ""

    results = run_backtest(start, end, label=label)
    print_summary(results, label=label, expected=expected)
    out = Path(args.csv) if args.csv else SCRIPT_DIR / "backtest_resultado.csv"
    save_csv(results, out)


if __name__ == "__main__":
    main()
