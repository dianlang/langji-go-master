# Project Guidelines

## Code Style
- Programmed in Python (Target runtime: Python 3.9+).
- Use `argparse` with sub-parsers inside `vajra_go.py` for CLI command management. When creating new bot actions, remember to add a new parser instead of standalone scripts.

## Architecture
- `vajra_go.py`: The single main entry point and CLI router for the application.
- `module_gbf/`: Contains specific mechanics for Granblue Fantasy (GBF)—handling game data extraction, constructing Wiki entities, and managing GBF asset logic.
- `module_huiji/`: Contains generic integration routines to interface with HuijiWiki (e.g., config loading, `pyvar_to_lua`, tabx updater, Danteng Excel formats).
- `module_once/`: Directory intended for single-use, temporary data conversion and migration scripts.

## Build and Executable
- **Install dependencies**: `pip install -r requirements.txt`. (Requires virtual environment setup in `venv`).
- **Configuration**: Always ensure `config.ini` is set up locally (derived from `config.ini.example`).
- **Build tool**: Use PyInstaller configured in [pack.bat](pack.bat) to create standalone executables. The build command is:
  ```cmd
  venv\Scripts\pyinstaller -c --onefile --version-file "VERSION_INFO" --workpath "build" --distpath "dist" --icon="res\vajra.ico" -y "vajra_go.py"
  ```

## Conventions
- Add new top-level features by extending `subparsers.add_parser()` in `vajra_go.py` and assign a callback via `extract_parser.set_defaults(callback=your_func)`.
- Centralized configurations are loaded via `config_loader('config.ini')` in the main script and should be passed to feature functions as argument payloads (`cfg`), bypassing scattered file parsing.
- Paths and directories to be created at runtime (e.g., `WIKITEXT_PATH`) should be managed from the root `config.py` definitions in `init()`.