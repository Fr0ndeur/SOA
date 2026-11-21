# Лабораторна робота №5 — безпека сервісних API

Скриншоти робіть у місцях, позначених у протоколі. Для об’єднаних перевірок залишайте результати послідовно в одному вікні термінала й робіть знімок після останньої перевірки. Не зменшуйте шрифт заради одного кадру: якщо фактичний вивід довгий, додайте сусідній знімок у тому самому місці протоколу. Стани до відмови та після відновлення, а також різні вкладки браузера знімайте окремо там, де це прямо зазначено.

Навчальний застосунок обліку сеансів містить три сервіси: профілі, трекінг, звіти. FastAPI API Gateway перевіряє JWT і scopes перед пересиланням запиту. Keycloak обслуговує OAuth 2.0 Authorization Code Grant із PKCE S256. Вебінтерфейс працює без npm та зовнішніх JavaScript CDN.

## Запуск

Потрібні Docker Desktop із Linux containers та Docker Compose. Із цієї папки:

```powershell
cd "C:\Users\Oleksii\Desktop\Masters\5th_year\SOA\Lab5"
docker version
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail 30 keycloak
```

Перший запуск Keycloak може тривати понад хвилину. Відкрийте [вебінтерфейс](http://localhost:8080/) лише після завершення імпорту realm `soa` та `upstream` у журналі. Адмінконсоль: [Keycloak](http://localhost:8180/admin/), `admin` / `admin-lab5`. Використовуйте саме `localhost`, оскільки redirect URI та issuer налаштовані на нього.

| Обліковий запис | Пароль | Дозволи |
|---|---|---|
| `user5` у realm `soa` | `User5-demo!` | `profile:read`, `tracking:read`, `tracking:write`, `reports:read` |
| `reader5` у realm `soa` | `Reader5-demo!` | три scopes читання; запис заборонено |
| `upstream5` у realm `upstream` | `Upstream5-demo!` | після входу через broker — лише читання у `soa` |

Це відкриті локальні демонстраційні паролі. `start-dev`, HTTP, demo client secret та довіра до email локального IdP призначені для лабораторного стенда. Для розгортання поза ним потрібні HTTPS, окреме керування секретами та виробнича конфігурація Keycloak. Backend-сервіси доступні лише у Docker-мережі; JWT перевіряє публічний gateway. Це не реалізація повного Zero Trust між сервісами.

## Перевірка в браузері

1. Відкрийте `http://localhost:8080/`, залиште чотири scopes увімкненими, натисніть «Увійти через Keycloak» та введіть `user5` / `User5-demo!` на сторінці Keycloak.
2. У DevTools → Network увімкніть Preserve log. Авторизаційний запит повинен містити `response_type=code`, `state`, `code_challenge`, `code_challenge_method=S256`. Після повернення SPA обмінює `code` через `grant_type=authorization_code` і `code_verifier`. У SPA немає client secret; password grant вимкнено.
3. Натисніть «JWT та видані scopes», «Отримати профіль» для ID `5`, «Додати сеанс», «Отримати сеанс», «Отримати звіт». ID створеного сеансу — UUID, він автоматично підставляється у форму. Проєкція звіту оновлюється асинхронно, тому для нового сеансу може знадобитися повторний запит.
4. Вийдіть через кнопку та підтвердьте завершення сеансу, якщо Keycloak запитає. Увійдіть як `reader5`, знову запитавши всі scopes. У відповіді `/auth/me` не повинно бути `tracking:write`; POST `/sessions` повертає `403`, запити читання — `200`.
5. Вийдіть. Виберіть лише `profile:read`, увійдіть як `user5`: профіль доступний, а звіт повертає `403`. Так перевіряється незалежність scopes від наявності широкої ролі.
6. Для broker натисніть «Увійти через навчальний OIDC IdP» і введіть `upstream5` / `Upstream5-demo!`. За потреби підтвердьте попередньо заповнений профіль під час першого входу. У realm `soa` створиться федеративний користувач із правами читання. Вхід через broker також доступний кнопкою на звичайній сторінці входу Keycloak.

`upstream` — окрема локальна OIDC-система ідентифікації, зовнішня щодо realm `soa`, але розміщена у тому самому процесі Keycloak. Вона дає відтворювану демонстрацію identity brokering без сторонньої реєстрації. Вона не є Atlassian, Google або іншим незалежним публічним провайдером. Якщо викладач вимагає саме сторонній SaaS, потрібно зареєструвати там OAuth-клієнт і замінити конфігурацію IdP; цей стенд такого підключення не заявляє.

## JWT і маршрути

| Метод і ресурс | Сервіс | Необхідний scope |
|---|---|---|
| `GET /users/5`, `GET /users?email=student5@example.com` | профілі | `profile:read` |
| `POST /sessions` | трекінг | `tracking:write` |
| `GET /sessions/{UUID}` | трекінг | `tracking:read` |
| `GET /reports/5` | звіти | `reports:read` |

`GET /auth/me` повертає лише перевірені claims. Без токена або з некоректним токеном gateway повертає `401`; із дійсним токеном без scope — `403`. Перевіряються підпис RS256 за JWKS, `iss=http://localhost:8180/realms/soa`, `aud=soa-api`, термін `exp`, наявність `sub` та `iat`. Перевірка підпису не зводиться до декодування Base64. Недоступність JWKS за відсутності придатного кешу дає `503`.

Окремі client scopes пов’язані з realm roles через `scopeMappings`. Тому запит широкого `scope` у URL сам по собі не видає право запису. `fullScopeAllowed=false`, PKCE S256 обов’язковий; implicit і direct access grants вимкнені. Access token зберігається лише в оперативній пам’яті вкладки, а тимчасові `state` і `code_verifier` — у `sessionStorage` до обміну коду. SPA не використовує claims ID token для доступу до API.

Scopes у цій ЛР задають доступ до ресурсів сервісу, а не володіння конкретним `user_id`. Читач із відповідним scope може читати навчальні записи користувачів 5 і 6. Для реальних персональних даних додатково потрібна перевірка власника або адміністративних повноважень.

## CORS

Використано `CORSMiddleware` фреймворку FastAPI/Starlette. Дозволені origins: `https://cad.kpi.ua` та `http://localhost:8080`; методи GET, POST, OPTIONS; заголовки Authorization і Content-Type. Gateway зберігає CORS-заголовки upstream та додає їх до власних відповідей, включно з `401` і `403`. Сервіси також мають middleware CORS.

У PowerShell використовуйте `curl.exe`, щоб уникнути alias `Invoke-WebRequest`:

```powershell
curl.exe -i -X OPTIONS http://localhost:8080/sessions -H "Origin: https://cad.kpi.ua" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: authorization,content-type"
curl.exe -i -X OPTIONS http://localhost:8080/sessions -H "Origin: https://attacker.example" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: authorization,content-type"
curl.exe -i http://localhost:8080/users/5 -H "Origin: https://cad.kpi.ua"
```

Очікування: перший preflight `200` з `Access-Control-Allow-Origin: https://cad.kpi.ua`; другий `400` без цього заголовка; третій `401` із дозволеним CORS origin. CORS регулює читання відповідей браузером і не замінює JWT-перевірку: curl та серверні клієнти не зобов’язані застосовувати CORS.

Для позитивного authenticated запиту натисніть «Копіювати access token» і локально підставте значення:

```powershell
$token = Read-Host 'Access token'
curl.exe -i http://localhost:8080/users/5 -H "Authorization: Bearer $token" -H "Origin: https://cad.kpi.ua"
```

Не додавайте токени до звіту чи репозиторію. Для скриншота приховайте повне значення токена; достатньо claims та статусу відповіді.

## Автоматичні перевірки

```powershell
python -m pip install -r security/requirements.txt
python -m unittest discover -s security/tests -v
python security/tests/check_login.py
```

`security/tests/test_security.py` містить 7 offline-тестів із реальною RSA-криптографією та підміною JWKS/HTTP upstream. Вони перевіряють відхилення неправильного підпису/issuer/audience/expiry, точне зіставлення scopes, allowlist маршрутів, CORS та конфігурацію читача. Це не доказ виконання імпорту Keycloak.

`security/tests/check_login.py` потребує запущеного стенда. Він проходить HTML-форму Keycloak як браузерний клієнт, отримує authorization code, обмінює його з PKCE, перевіряє заборону повторного використання code, роботу `user5`, `reader5` і реальний OIDC broker-вхід `upstream5`, API та CORS. Grant залишається `authorization_code`, хоча форму заповнює тестовий HTTP-клієнт. Скрипт додає один сеанс користувачу 5. Для фіксованого HTTP localhost тестовий cookie jar відтворює поведінку браузера щодо Secure cookies. Браузерний JavaScript UI має окремий ручний сценарій.

Під час підготовки успішно виконано 7 offline-тестів і live-скрипт: `user5` — профіль 200, запис 202, звіт 200; `reader5` та `upstream5` через broker — профіль 200, запис 403, звіт 200. Окремо обидва JSON-файли імпортовано у тимчасові realm; на чистому імпорті перевірено `basic` scope, наявність `sub` і правильне розмежування scopes двох користувачів. Тимчасові realm після перевірки видалено. Самі скриншоти та ручну демонстрацію SPA слід виконати власноруч.

## Відтворення та зупинка

```powershell
docker compose logs --tail 100 gateway keycloak
docker compose down
```

Realm JSON імпортується на старті лише за відсутності realm. Keycloak зберігає стан у томі `keycloak-data`, тому перезапуск або відтворення контейнера не імпортує змінений JSON поверх наявного realm. Налаштування можна змінити в адмінконсолі; для повного нового імпорту потрібне свідоме скидання локального стану. `docker compose down -v` видаляє всі томи й дані навчального стенда — застосовуйте лише для свідомого повного скидання.

Якщо Docker повідомляє `all predefined address pools have been fully subnetted`, перевірте наявні Docker-мережі та конфлікт адрес; у compose стенда задано окрему підмережу. За конфлікту з локальною мережею змініть її на вільну. Не видаляйте чужі мережі або томи.

## Скриншоти для протоколу

| № | Що показати |
|---|---|
| 1 | Контейнери стенда після запуску: `docker compose ps`. |
| 2 | Realm `soa` у Keycloak Admin Console, client `soa-browser`. |
| 3 | Два послідовні preflight-запити в одному вікні: дозволений origin із CORS-заголовками та відхилений сторонній origin. |
| 4 | SPA перед входом із вибором scopes. |
| 5 | Authorization request у DevTools: `response_type=code` та PKCE S256. |
| 6 | Вхід у Keycloak і успішний token exchange; повні токени приховано. |
| 7 | Налаштування `campus-demo` та кнопка зовнішнього входу. |
| 8 | Upstream login і пов’язаний federated identity користувача у realm `soa`. |
| 9 | Перевірені claims користувача `user5` у `/auth/me`. |
| 10 | Успішний POST `/sessions` і UUID створеного сеансу. |
| 11 | `reader5`: відсутній scope запису та відповідь 403. |
| 12 | `user5` лише з `profile:read`: профіль 200, звіт 403. |
| 13 | Запит без access token: HTTP 401 із CORS-заголовками. |
| 14 | Підсумки offline- і live-тестів в одному вікні термінала після фактичного запуску стенда. |

## Джерела

- [Умова роботи](../ЛР5_SOA.pdf)
- [Keycloak: поточна версія 26.8.1 на дату підготовки](https://www.keycloak.org/downloads)
- [Keycloak: контейнери](https://www.keycloak.org/server/containers)
- [Keycloak: імпорт realm](https://www.keycloak.org/server/importExport)
- [Keycloak: client scopes, role mappings, identity brokering](https://www.keycloak.org/docs/latest/server_admin/index.html)
- [Keycloak: hostname та backchannel](https://www.keycloak.org/server/hostname)
- [FastAPI: CORS](https://fastapi.tiangolo.com/tutorial/cors/)
