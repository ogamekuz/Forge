"""desktop — пульт Forge 3.0 на PySide6 (слой interface: никто его не импортирует).

Платформа — Qt-шелл,
тяжёлое — в фоновых потоках (``widgets.run_bg``), мутации Qt — только в UI-потоке; статус —
раз в 3 с через ``StatusHub``; геометрия окна и последняя вкладка — в ``.forge/desktop.ini``.
Вид — «космос EVE Online»: стеклянные панели с уголками-скобами, cyan Photon + золото Omega.

Все расчёты — через ``forge.web.service.ForgeService`` (тот же фасад, что у сервера отчётов);
HTML-отчёты по стройкам отдаёт встроенный локальный сервер (``server.ReportServer``).
Запуск: ``pythonw -m forge.desktop`` (ярлык «Forge 3.0», ``Forge.cmd``).
"""
