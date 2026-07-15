#!/usr/bin/env python3
"""
Diagnose infeasibility by running Gurobi feasRelax on the saved LP file.
Run on INARI: python -u scripts/diagnose_infeasibility.py
"""
import gurobipy as grb
from pathlib import Path

LP_PATH = Path("results/diagnostics/iis_model_2050.lp")

print(f"Loading model from {LP_PATH} ...")
m = grb.read(str(LP_PATH))
print(f"  {m.NumVars} variables, {m.NumConstrs} constraints")

# First: confirm infeasible
m.setParam("DualReductions", 0)
m.setParam("InfUnbdInfo", 1)
m.setParam("Threads", 16)
m.setParam("Method", 2)
m.optimize()
print(f"\nStatus: {m.Status}  (3=infeasible, 5=unbounded)")

if m.Status != 2:  # not optimal
    print("\n=== Running feasRelax ===")
    constrs = m.getConstrs()
    rhspen = [1.0] * len(constrs)
    feasobj = m.feasRelax(0, True, None, None, None, constrs, rhspen)
    m.optimize()
    print(f"\nfeasRelax objective (total violation): {feasobj}")

    # Extract artificial variables
    art_vars = []
    for v in m.getVars():
        vn = v.VarName
        if ("ArtP_" in vn or "ArtN_" in vn) and abs(v.X) > 1e-6:
            art_vars.append((vn, v.X))

    art_vars.sort(key=lambda x: abs(x[1]), reverse=True)
    print(f"Violated constraints: {len(art_vars)}")

    # Group by constraint type
    from collections import Counter
    type_counts = Counter()
    type_total_viol = Counter()
    for vn, val in art_vars:
        # Extract constraint type from name like ArtP_operation_adequacy_constraint[...]
        clean = vn.replace("ArtP_", "").replace("ArtN_", "")
        ctype = clean.split("[")[0] if "[" in clean else clean
        type_counts[ctype] += 1
        type_total_viol[ctype] += abs(val)

    print("\n=== Violations by constraint type ===")
    for ctype, count in type_counts.most_common(20):
        total = type_total_viol[ctype]
        print(f"  {ctype}: {count} violations, total magnitude = {total:.2f}")

    # Top 50 individual violations
    print("\n=== Top 50 individual violations ===")
    for vn, val in art_vars[:50]:
        print(f"  {vn}: {val:.6f}")

    # Save full list
    out_path = Path("results/diagnostics/feasrelax_2050.txt")
    with open(out_path, "w") as f:
        f.write(f"feasRelax objective: {feasobj}\n")
        f.write(f"Violated constraints: {len(art_vars)}\n\n")
        f.write("=== By type ===\n")
        for ctype, count in type_counts.most_common():
            f.write(f"  {ctype}: {count} violations, total = {type_total_viol[ctype]:.2f}\n")
        f.write("\n=== All violations ===\n")
        for vn, val in art_vars:
            f.write(f"  {vn}: {val:.6f}\n")
    print(f"\nFull details saved to {out_path}")

else:
    print("Model is feasible! Something changed.")
