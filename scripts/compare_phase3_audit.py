"""Compare Phase 3 fused-panel Opus audit against v2 baseline."""
import json, pathlib

PANEL = [
    "DE-101(I) Application for Informal - Intestate (Rev. 09-12-19)",
    "DE-104 PR Acceptance (Rev. 07-01-19)",
    "PP-205 Joined Petition for Guardian and Conservator (Rev. 07-01-19)",
    "NC-001 Petition for Name Change of Minor",
    "DE-405 Inventory (Rev. 5-6-21)",
]

V2_DIR = pathlib.Path("reports/opus-alignment-layer1-full")
FUSED_DIR = pathlib.Path("reports/opus-alignment-fused-panel")


def counts(path):
    if not path.exists():
        return None
    d = json.loads(path.read_text())
    a = n = m = 0
    for pg in d.get("pages", []):
        for issue in pg.get("issues", []):
            t = issue.get("type", "")
            if t == "alignment": a += 1
            elif t == "naming":  n += 1
            elif t == "missing": m += 1
    return (a + n + m, a, n, m)


def main():
    print(f"{'form':52s}  {'v2 (A/N/M)':>16}  {'fused (A/N/M)':>16}  {'delta':>6}")
    v2_tot = f_tot = 0
    for stem in PANEL:
        v2 = counts(V2_DIR / f"{stem}.json")
        fu = counts(FUSED_DIR / f"{stem}_fused.json")
        v2_str = f"{v2[0]} ({v2[1]}/{v2[2]}/{v2[3]})" if v2 else "N/A"
        if not fu:
            print(f"{stem[:52]:52s}  {v2_str:>16}  {'PENDING':>16}  ")
            continue
        delta = fu[0] - v2[0]
        f_str = f"{fu[0]} ({fu[1]}/{fu[2]}/{fu[3]})"
        print(f"{stem[:52]:52s}  {v2_str:>16}  {f_str:>16}  {delta:+5d}")
        v2_tot += v2[0] if v2 else 0
        f_tot += fu[0]
    if v2_tot or f_tot:
        print(f"{'TOTAL':52s}  {str(v2_tot):>16}  {str(f_tot):>16}  {f_tot - v2_tot:+5d}")


if __name__ == "__main__":
    main()
