# SSH SOCKS5 port forwarding runbook

Этот документ фиксирует операционную настройку маршрута для будущих Telegram API/Telethon вызовов через SSH-туннель.

## 1) Базовый запуск туннеля

На хосте, где запущен бот:

```bash
ssh -N -D 127.0.0.1:1080 user@remote-server
```

- `-D 127.0.0.1:1080` — локальный SOCKS5-прокси.
- `-N` — не запускать удалённую команду, только туннель.

## 2) Проверки на удалённой стороне

На `remote-server`:

- SSH-доступ открыт.
- В `sshd_config` включено:
  ```text
  AllowTcpForwarding yes
  ```
- firewall не блокирует исходящие соединения к целевым сервисам.

## 3) Автовосстановление через systemd + autossh

Пример юнита `/etc/systemd/system/bot-socks-tunnel.service`:

```ini
[Unit]
Description=SSH SOCKS5 tunnel for statostics-tgbot
After=network-online.target
Wants=network-online.target

[Service]
User=bot
Environment="AUTOSSH_GATETIME=0"
ExecStart=/usr/bin/autossh -M 0 -N -D 127.0.0.1:1080 user@remote-server \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Применение:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bot-socks-tunnel.service
sudo systemctl status bot-socks-tunnel.service
```

## 4) Проверка, что прокси работает

```bash
curl --socks5-hostname 127.0.0.1:1080 https://api.ipify.org
```

Если команда возвращает IP удалённой стороны (или цепочки далее по маршруту), туннель работает.

## 5) Как использовать позже в коде

Для будущих интеграций Telegram API/Telethon используйте адрес прокси:

```text
socks5://127.0.0.1:1080
```

Схема туннеля при этом не меняется.
