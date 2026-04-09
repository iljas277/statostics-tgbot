# SSH SOCKS5 port forwarding runbook

Этот документ фиксирует операционную настройку маршрута для Telegram Bot API вызовов через SSH-туннель.

## 1) Установка autossh

Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y autossh
```

## 2) Базовый запуск туннеля

На хосте, где запущен бот:

```bash
ssh -N -D 127.0.0.1:1080 user@remote-server
```

- `-D 127.0.0.1:1080` — локальный SOCKS5-прокси.
- `-N` — не запускать удалённую команду, только туннель.

## 3) Проверки на удалённой стороне

На `remote-server`:

- SSH-доступ открыт.
- В `sshd_config` включено:
  ```text
  AllowTcpForwarding yes
  ```
- firewall не блокирует исходящие соединения к целевым сервисам.

## 4) Автовосстановление через systemd + autossh

Пример юнита `/etc/systemd/system/bot-socks-tunnel.service`:

```ini
[Unit]
Description=SSH SOCKS5 tunnel for statistics-tgbot
After=network-online.target
Wants=network-online.target

[Service]
# Replace with an existing Linux user that owns valid SSH keys for remote-server.
User=bot
Environment="AUTOSSH_GATETIME=0"
# Replace user@remote-server with your real SSH user and host.
ExecStart=/usr/bin/autossh -M 0 -N -D 127.0.0.1:1080 user@remote-server \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o StrictHostKeyChecking=yes \
  -o ExitOnForwardFailure=yes
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Готовый шаблон этого юнита в репозитории: `infra/systemd/bot-socks-tunnel.service`.

Применение (копируем шаблон в `/etc/systemd/system/` и включаем сервис):

```bash
sudo cp infra/systemd/bot-socks-tunnel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bot-socks-tunnel.service
```

`enable --now` в одной команде одновременно включает автозапуск и сразу стартует сервис.

Проверка статуса после запуска:

```bash
sudo systemctl status bot-socks-tunnel.service
```

Онлайн-мониторинг логов (блокирует терминал до `Ctrl+C`):

```bash
journalctl -u bot-socks-tunnel.service -f
```

## 5) Проверка, что прокси работает

```bash
curl --socks5-hostname 127.0.0.1:1080 https://api.ipify.org
```

Если команда возвращает IP удалённой стороны (или цепочки далее по маршруту), туннель работает.

## 6) Как использовать позже в коде

Для интеграций Telegram Bot API используйте адрес прокси:

```text
socks5://127.0.0.1:1080
```

Схема туннеля при этом не меняется.
