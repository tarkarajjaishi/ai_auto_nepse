# chukul_data

## Language rule

**Python only.** Every part of this project is written in Python — no other
programming language, no exceptions.

Not allowed: JavaScript/TypeScript, Go, Rust, Java, C/C++/C#, Ruby, PHP,
shell/PowerShell scripts as project logic.

Allowed (not programming languages): Markdown, JSON/YAML/TOML/INI config,
`.env`, SQL inside Python, plain CSV/text data.

Python libraries that ship their own JavaScript (Streamlit, Plotly) are fine —
that is the library's code, not ours. We never write JavaScript in this repo.

If a task seems to need another language, solve it in Python or say it can't be
done — do not introduce a second language.

## Storage rule

**`.txt` only.** There is no database. Everything the project saves goes into
plain `.txt` files.

Not allowed: SQLite or any DB server, `.csv`, `.json`, `.parquet`, `.pkl`,
`.xlsx`, `.db`, `.h5` as storage.

Exception: config files (`.env`, `.toml`/`.yaml` settings) are not data — they
stay as they are.

Write with stdlib `open()`. If a task seems to need a real DB, do it with `.txt`
or say it can't be done.
