/* Forge.app/Contents/MacOS/Forge — запускалка пульта Forge на macOS.
 *
 * Настоящий исполняемый файл бандла, а не shell-скрипт: Dock, строка меню и «Завершить» зовут
 * программу «Forge», а не «Python», и иконка в Dock — своя. Поднимает встроенный Python из
 * Contents/Resources/python (изолированно: PYTHON* из окружения и ~/.local не подмешиваются)
 * и выполняет Contents/Resources/boot.py — вся логика старта там.
 *
 * Собирает tools/build_macos.py: clang … -lpython3.12 -rpath @executable_path/../Resources/python/lib
 */
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fail(PyStatus st, PyConfig *cfg)
{
    PyConfig_Clear(cfg);
    if (PyStatus_IsExit(st))
        return st.exitcode;
    Py_ExitStatusException(st); /* печатает ошибку и завершает процесс */
    return 1;
}

static int set_path(PyConfig *cfg, wchar_t **field, const char *contents, const char *rel)
{
    char buf[PATH_MAX];
    if (snprintf(buf, sizeof buf, "%s/%s", contents, rel) >= (int)sizeof buf)
        return 0;
    return !PyStatus_Exception(PyConfig_SetBytesString(cfg, field, buf));
}

int main(int argc, char *argv[])
{
    char exe[PATH_MAX], contents[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || realpath(exe, contents) == NULL) {
        fputs("Forge: cannot resolve the executable path\n", stderr);
        return 1;
    }
    for (int i = 0; i < 2; i++) { /* …/Forge.app/Contents/MacOS/Forge → …/Forge.app/Contents */
        char *slash = strrchr(contents, '/');
        if (slash == NULL) {
            fputs("Forge: unexpected bundle layout\n", stderr);
            return 1;
        }
        *slash = '\0';
    }

    PyPreConfig pre;
    PyPreConfig_InitIsolatedConfig(&pre);
    pre.utf8_mode = 1; /* из Finder/Dock LANG не задан — без этого Python жил бы в локали C */
    PyStatus st = Py_PreInitialize(&pre);
    if (PyStatus_Exception(st))
        Py_ExitStatusException(st);

    PyConfig cfg;
    PyConfig_InitIsolatedConfig(&cfg);
    cfg.write_bytecode = 0; /* .pyc собраны при сборке; бандл (и его подпись) не трогаем */
    if (!set_path(&cfg, &cfg.home, contents, "Resources/python") ||
        !set_path(&cfg, &cfg.executable, contents, "Resources/python/bin/python3") ||
        !set_path(&cfg, &cfg.run_filename, contents, "Resources/boot.py")) {
        PyConfig_Clear(&cfg);
        fputs("Forge: cannot configure the embedded Python\n", stderr);
        return 1;
    }
    st = PyConfig_SetBytesArgv(&cfg, argc, argv);
    if (PyStatus_Exception(st))
        return fail(st, &cfg);
    st = Py_InitializeFromConfig(&cfg);
    if (PyStatus_Exception(st))
        return fail(st, &cfg);
    PyConfig_Clear(&cfg);
    return Py_RunMain();
}
