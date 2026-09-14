# Bank_of_mum

## Ubuntu server deployment

Verified on **14 September 2026** against the listeners, user systemd services,
Docker port mappings and deployment registry on `192.168.1.249`.

| Endpoint | Host TCP port | LAN URL |
|---|---:|---|
| Web | 5080 | http://192.168.1.249:5080/ |
| API | 5082 | http://192.168.1.249:5082/ |

Checkout: `/home/zageabb/flask/Bank_of_mum`.

These are **user** systemd units. Inspect them with:

```bash
systemctl --user status bank-of-mum-api.service bank-of-mum-web.service
systemctl --user cat bank-of-mum-api.service bank-of-mum-web.service
```

Local verification URL: `http://127.0.0.1:5082/api/health`. HTTP 200 was observed during this audit.

Development defaults and container-internal ports elsewhere in this repository
may differ from this host deployment. Use the live ports above when accessing
this Ubuntu server; do not start a second copy on a port already occupied.

[Complete Ubuntu port inventory](https://github.com/zageabb/universal-deployment-agent/blob/main/UBUNTU_PORTS.md).

