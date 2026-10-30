"""Ответы панели Remnawave 3.4.5 в той форме, что описана в её контракте.

Источник формы: github.com/remnawave/backend, тег 3.4.5, `libs/contract/`:
- `models/users.schema.ts` + `models/extended-users.schema.ts` + `models/user-traffic.schema.ts`
  (пользователь с subscriptionUrl, activeInternalSquads, userTraffic);
- `commands/users/get-users-stream.command.ts` ({users, nextCursor, hasMore});
- `models/hwid-user-device.schema.ts`, `commands/hwid/get-user-hwid-devices.command.ts`;
- `models/nodes.schema.ts`, `models/internal-squad.schema.ts`;
- `commands/system/get-metadata.command.ts`;
- `models/subscription-settings.schema.ts` + `models/subscription-settings/*.schema.ts`,
  `commands/subscription-settings/*.command.ts` (GET и PATCH `/api/subscription-settings`);
- `constants/errors/errors.ts` (A019 — 400, A025 — 404, A029/A030 — 400).
Все ответы панели завёрнуты в {"response": ...}.
"""

from typing import Any

TEST_SQUAD_UUID = "11111111-1111-4111-8111-111111111111"
DEFAULT_SQUAD_UUID = "22222222-2222-4222-8222-222222222222"
SQUAD_UUIDS = {"test": TEST_SQUAD_UUID, "Default-Squad": DEFAULT_SQUAD_UUID}

STAMP = "2026-10-24T10:00:00.000Z"


def user(
    *,
    user_id: int = 7,
    username: str = "tg_100",
    telegram_id: int | None = 100,
    status: str = "ACTIVE",
    expire_at: str = "2026-11-24T10:00:00.000Z",
    squads: tuple[str, ...] = ("Default-Squad",),
    short_uuid: str = "AbCdEfGh12345678",
    first_connected_at: str | None = None,
    used_bytes: int = 0,
    traffic_limit_bytes: int = 0,
    device_limit: int | None = 3,
) -> dict[str, Any]:
    return {
        "id": user_id,
        "shortUuid": short_uuid,
        "username": username,
        "status": status,
        "trafficLimitBytes": traffic_limit_bytes,
        "trafficLimitStrategy": "NO_RESET",
        "expireAt": expire_at,
        "telegramId": telegram_id,
        "email": None,
        "description": None,
        "tag": None,
        "hwidDeviceLimit": device_limit,
        "externalSquadUuid": None,
        "trojanPassword": "trojanpassword123",
        "vlessUuid": "33333333-3333-4333-8333-333333333333",
        "ssPassword": "sspassword12345",
        "lastTriggeredThreshold": 0,
        "subRevokedAt": None,
        "lastTrafficResetAt": None,
        "createdAt": STAMP,
        "updatedAt": STAMP,
        "subscriptionUrl": f"https://sub.ts-proxy.com/{short_uuid}",
        "activeInternalSquads": [{"uuid": SQUAD_UUIDS[name], "name": name} for name in squads],
        "userTraffic": {
            "usedTrafficBytes": used_bytes,
            "lifetimeUsedTrafficBytes": used_bytes,
            "onlineAt": first_connected_at,
            "firstConnectedAt": first_connected_at,
            "lastConnectedNodeUuid": None,
        },
    }


def device(hwid: str = "hwid-abc-123", *, user_id: int = 7) -> dict[str, Any]:
    return {
        "hwid": hwid,
        "userId": user_id,
        "platform": "iOS",
        "osVersion": "18.1",
        "deviceModel": "iPhone 15",
        "userAgent": "Happ/3.0",
        "requestIp": "203.0.113.5",
        "createdAt": STAMP,
        "updatedAt": STAMP,
    }


def node(name: str = "Нидерланды", *, connected: bool = True) -> dict[str, Any]:
    return {
        "uuid": "44444444-4444-4444-8444-444444444444",
        "id": 1,
        "name": name,
        "address": "203.0.113.10",
        "port": 2222,
        "proxyUrl": None,
        "isConnected": connected,
        "isDisabled": False,
        "isConnecting": False,
        "lastStatusChange": STAMP,
        "lastStatusMessage": None if connected else "Connection lost",
        "isTrafficTrackingActive": False,
        "trafficResetDay": None,
        "trafficLimitBytes": None,
        "trafficUsedBytes": None,
        "notifyPercent": None,
        "viewPosition": 1,
        "countryCode": "NL",
        "consumptionMultiplier": 1,
        "nodeConsumptionMultiplier": 1,
        "tags": [],
        "integrationUuids": [],
        "ips": [],
        "createdAt": STAMP,
        "updatedAt": STAMP,
        "configProfile": {"activeConfigProfileUuid": None, "activeInbounds": []},
        "providerUuid": None,
        "provider": None,
        "activePluginUuid": None,
        "system": None,
        "versions": None,
        "xrayUptime": 0,
        "usersOnline": 4,
        "note": None,
    }


def squads() -> dict[str, Any]:
    return {
        "total": 2,
        "internalSquads": [
            {
                "uuid": uuid,
                "viewPosition": index,
                "name": name,
                "tags": [],
                "info": {"membersCount": 1, "inboundsCount": 1},
                "inbounds": [],
                "createdAt": STAMP,
                "updatedAt": STAMP,
            }
            for index, (name, uuid) in enumerate(SQUAD_UUIDS.items())
        ],
    }


def metadata(version: str = "3.4.5") -> dict[str, Any]:
    return {
        "version": version,
        "build": {"time": STAMP, "number": "1"},
        "git": {
            "backend": {"commitSha": "abc", "branch": "main", "commitUrl": "https://example"},
            "frontend": {"commitSha": "def", "commitUrl": "https://example"},
        },
    }


SETTINGS_UUID = "55555555-5555-4555-8555-555555555555"


def subscription_settings(headers: dict[str, str] | None) -> dict[str, Any]:
    """Настройки подписки: свои заголовки ответа (`customResponseHeaders`) и всё остальное."""
    return {
        "uuid": SETTINGS_UUID,
        "serveJsonAtBaseSubscription": False,
        "isShowCustomRemarks": True,
        "customRemarks": {
            "expiredUsers": ["⌛ Subscription expired"],
            "limitedUsers": ["🚧 Traffic limit reached"],
            "disabledUsers": ["🚫 Subscription disabled"],
            "emptyHosts": ["→ No hosts found"],
            "HWIDMaxDevicesExceeded": ["Max devices reached"],
            "HWIDNotSupported": ["App not supported"],
        },
        "customResponseHeaders": headers,
        "randomizeHosts": False,
        "responseRules": {
            "version": "1",
            "rules": [
                {
                    "name": "Browser",
                    "description": "Open subscription page in browser",
                    "enabled": True,
                    "operator": "AND",
                    "conditions": [
                        {
                            "headerName": "accept",
                            "operator": "CONTAINS",
                            "value": "text/html",
                            "caseSensitive": True,
                        }
                    ],
                    "responseType": "BROWSER",
                }
            ],
        },
        "hwidSettings": {"enabled": True, "fallbackDeviceLimit": 3, "maxDevicesAnnounce": None},
        "createdAt": STAMP,
        "updatedAt": STAMP,
    }


def error(code: str, message: str, path: str = "/api/users") -> dict[str, Any]:
    return {"timestamp": STAMP, "path": path, "message": message, "errorCode": code}
