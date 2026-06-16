# Native code placeholder

Place C or C++ sources here if your algorithm needs them.

Recommended convention:
- keep sources under `native/`
- add your own `build.sh`, `CMakeLists.txt`, or `build.py`
- call the build step from `solver.py`
- expose only the Python `algorithm()` entrypoint from `__init__.py`
