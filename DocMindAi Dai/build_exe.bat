@echo off
echo ============================================
echo  DOCMind AI - Build Trial .exe
echo ============================================
echo.

REM Activate venv
call .venv\Scripts\activate

REM Install PyInstaller if needed
pip install pyinstaller --quiet

echo Building executable...
pyinstaller ^
  --onedir ^
  --noconsole ^
  --name "DocMindAI" ^
  --icon "static\favicon.ico" ^
  --add-data "templates;templates" ^
  --add-data "static;static" ^
  --add-data "trial_config.py;." ^
  --add-data "trial_guard.py;." ^
  --add-data ".env;." ^
  --hidden-import "sklearn.utils._cython_blas" ^
  --hidden-import "sklearn.neighbors.typedefs" ^
  --hidden-import "sklearn.neighbors.quad_tree" ^
  --hidden-import "sklearn.tree._utils" ^
  --hidden-import "sentence_transformers" ^
  --hidden-import "faiss" ^
  --collect-all "sentence_transformers" ^
  --collect-all "transformers" ^
  app.py

echo.
echo ============================================
echo  Done! Find your .exe in: dist\DocMindAI\
echo  Zip the entire DocMindAI folder and send!
echo ============================================
pause
