"""Generate gridlock_colab.ipynb from gridlock_pipeline.py (single source of truth).

Cell markers in the source:
  # %%              -> code cell
  # %% [markdown]   -> markdown cell (following '# ' comment lines = markdown body)

Run:  python make_notebook.py
"""
import json, re, sys, os

SRC = os.path.join(os.path.dirname(__file__), "gridlock_pipeline.py")
OUT = os.path.join(os.path.dirname(__file__), "gridlock_colab.ipynb")

def parse_cells(text):
    lines = text.splitlines()
    cells, cur, kind = [], [], None
    def flush():
        if kind is None:
            return
        body = cur[:]
        # trim leading/trailing blank lines
        while body and body[0].strip() == "":
            body.pop(0)
        while body and body[-1].strip() == "":
            body.pop()
        if not body:
            return
        if kind == "markdown":
            md = [re.sub(r"^# ?", "", ln) for ln in body]
            cells.append(("markdown", md))
        else:
            cells.append(("code", body))
    for ln in lines:
        if ln.startswith("# %% [markdown]"):
            flush(); cur, kind = [], "markdown"
        elif ln.startswith("# %%"):
            flush(); cur, kind = [], "code"
        else:
            if kind is not None:
                cur.append(ln)
    flush()
    return cells

def to_ipynb(cells):
    nb_cells = []
    for kind, body in cells:
        if kind == "code":
            # notebook-only: activate shell/magic lines commented out in the .py
            body = [re.sub(r"^# (!|%)", r"\1", l) for l in body]
        src = [l + "\n" for l in body[:-1]] + [body[-1]]
        if kind == "markdown":
            nb_cells.append({"cell_type": "markdown", "metadata": {}, "source": src})
        else:
            nb_cells.append({"cell_type": "code", "metadata": {},
                             "execution_count": None, "outputs": [], "source": src})
    return {
        "cells": nb_cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "colab": {"provenance": [], "toc_visible": True},
        },
        "nbformat": 4, "nbformat_minor": 5,
    }

def main():
    text = open(SRC, encoding="utf-8").read()
    cells = parse_cells(text)
    nb = to_ipynb(cells)
    json.dump(nb, open(OUT, "w", encoding="utf-8"), indent=1)
    n_md = sum(1 for c in cells if c[0] == "markdown")
    n_code = sum(1 for c in cells if c[0] == "code")
    print(f"Wrote {OUT}: {n_md} markdown + {n_code} code cells")

if __name__ == "__main__":
    main()
