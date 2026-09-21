# Benchmark Pulse

**Read `details.md` before changing anything.** It holds the architecture, the
three return measures and which screen uses which, and the decisions that
look wrong until you know why — several of the obvious "improvements" here have
already been tried and reverted for stated reasons.

## The four things to know immediately

1. **This is the canonical, git-tracked copy.** It started as a straight copy
   of an earlier OneDrive-synced working folder (which blocked new-file
   creation from tooling), but that copy is now retired — all further work
   happens here, and this is what's pushed to
   `https://github.com/391391391/Benchmark-Pulse.git`. Everything created at
   runtime goes to the data root that `paths.py` resolves — in this location
   that's the project's own `data/` folder, since nothing here blocks a new
   file.

2. **`.env` holds a live Gemini API key.** It is gitignored. Never commit it,
   never print it, never copy it into a shared file. It has never been
   committed to this repo's history — keep it that way.

3. **The venv is outside the project:**
   ```
   C:\dev\bp-venv\Scripts\python.exe -m pytest tests/ -q
   C:\dev\bp-venv\Scripts\python.exe -m streamlit run app/streamlit_app.py --server.address 127.0.0.1
   ```

4. **Three return measures are in use deliberately.** Direct Alpha
   (money-weighted, whole-life) on the KPI strip and Portfolio vs benchmark; the
   6M/1Y/3Y trend score (time-weighted, cashflow-independent) on Winners and
   laggards and Holding analysis; Modified Dietz for the portfolio's 1Y/3Y window
   return. Mixing them produces numbers that look broken. §4 of `details.md`
   explains which belongs where and why.

## House style

Figures are computed by the engine, never by a model. The model is used only
for judgement calls — benchmark selection and its rationale, news categories,
a sector the price feed cannot supply — and every one of those is labelled on
screen with its source. When a comparison is not fair, or a number is missing,
the screen says so rather than leaving a gap for the reader to misread.
