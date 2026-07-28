# TeamSpeak 3 permissions

Полный список имён (`permsid`) — в [`TS3_PERMISSIONS.csv`](TS3_PERMISSIONS.csv)
(источник: [ReSpeak/tsdeclarations](https://github.com/ReSpeak/tsdeclarations/blob/master/Permissions.csv)).

Числовые `permid` **меняются между версиями сервера**. Имена стабильны.
Сидер берёт актуальный список с живого сервера командой `permissionlist`.

## Важно для гильдии

| Нужно | Правильный permsid | Неверно |
|--------|---------------------|---------|
| Перетаскивать | `i_client_move_power` / `i_client_needed_move_power` | ~~`b_client_move`~~ (не существует) |
| Kick из канала | `i_client_kick_from_channel_power` | ~~`b_client_kick_from_channel`~~ |
| Kick с сервера | `i_client_kick_from_server_power` | ~~`b_client_kick_from_server`~~ |
| Бан | `b_client_ban_create`, `i_client_ban_power`, `i_client_ban_max_bantime` | — |
| Вход в канал | `i_channel_join_power` vs channel `i_channel_needed_join_power` | — |
| Выдача групп | `i_group_member_add_power` / `i_group_needed_member_add_power` | — |

Онлайн-справочник: https://ts3index.com/permlist/
