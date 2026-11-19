# Лабораторна робота 3 — виконання

Команди виконуються у PowerShell. `irm` — скорочення Invoke-RestMethod, `curl.exe` запускає саме curl, а не однойменний псевдонім PowerShell. Після створення сесії її відповідь зберігається у змінній, щоб не переписувати UUID вручну.

Перед запуском відкрийте Docker Desktop. Іншу лабораторну на порту 8080 спочатку зупиніть через `docker compose down` у її папці. Збережені дані залишаються між запусками: порівнюйте зміну підсумку, а не очікуйте порожню базу.

Знімки робіть після відповідних перевірок. Якщо результат ще pending, зачекайте й повторіть лише читання його стану. Повторний POST створює іншу сесію.

## Запуск

```powershell
cd "C:\Users\Oleksii\Desktop\Masters\5th_year\SOA\Lab3"
docker version
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Дочекайтеся healthy для RabbitMQ та доступності сервісів.

Вставити скриншот 1 — Запущені контейнери та healthy RabbitMQ після перевірки конфігурації.

## Профіль користувача

```powershell
curl.exe http://localhost:8080/health
curl.exe http://localhost:8080/users/5
start http://localhost:8080/docs
```

Профіль має містити користувача 5. Документація відкриється у браузері.

Вставити скриншот 2 — профіль користувача 5 та документація Swagger доступні.

## Запис сесії

```powershell
curl.exe http://localhost:8080/reports/5
$session = irm http://localhost:8080/sessions -Method Post -ContentType application/json -Body '{"user_id":5,"duration_seconds":600}'
$session
sleep 3
curl.exe "http://localhost:8080/sessions/$($session.id)"
curl.exe http://localhost:8080/reports/5
docker compose logs --tail 20 tracking reporting
```

Початкова відповідь містить pending. Після обробки має бути completed, а підсумок збільшується на 600 секунд. Якщо ще pending, повторіть GET сесії та звіту за кілька секунд.

Вставити скриншот 3 — POST повертає pending, стан стає completed, звіт збільшується на 600 секунд; логи показують Tracking → Profiles і подію → Reporting.

## Черги RabbitMQ

```powershell
start http://localhost:15672
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready consumers
```

У браузері увійдіть як student / student. Відкрийте Queues and Streams та обмін sessions. Після обробки черги можуть бути порожніми.

Вставити скриншот 4 — RabbitMQ Management доступний з логіном student і паролем student; черги reports, results, session.dlq існують.

## Дані локальних транзакцій

```powershell
curl.exe http://localhost:8080/debug/state
docker compose exec reporting python
```

У відкритій Python-консолі контейнера виконайте:

```python
import storage
storage.rows("SELECT * FROM projection")
storage.rows("SELECT * FROM inbox")
storage.rows("SELECT * FROM outbox")
exit()
```

Ідентифікатор сесії пов’язує первинний запис, проєкцію та повідомлення.

Вставити скриншот 5 — у Tracking є sessions та sent outbox; у Reporting є проєкція, inbox та результат outbox з тим самим id сесії.

## Помилка запису звітності

```powershell
curl.exe http://localhost:8080/reports/5
$failed = irm http://localhost:8080/sessions -Method Post -ContentType application/json -Body '{"user_id":5,"duration_seconds":120,"fail":true}'
$failed
sleep 3
curl.exe "http://localhost:8080/sessions/$($failed.id)"
curl.exe http://localhost:8080/reports/5
docker compose logs --tail 20 reporting tracking
```

Сесія переходить у compensated, підсумок звіту не змінюється. Якщо стан ще pending, повторіть читання за кілька секунд.

Вставити скриншот 6 — сесія compensated, сума звіту незмінна, логи містять session.dlq та saga_compensated.

## Недоступний брокер

```powershell
docker compose stop rabbitmq
$response = iwr http://localhost:8080/sessions -UseBasicParsing -Method Post -ContentType application/json -Body '{"user_id":5,"duration_seconds":75}'
$response.StatusCode
$response.Content
$offline = ($response.Content | ConvertFrom-Json).id
curl.exe http://localhost:8080/debug/state
docker compose logs --tail 10 tracking
```

iwr — скорочення Invoke-WebRequest. Тут воно потрібне, щоб окремо показати HTTP 202 і тіло відповіді. У базі має залишитися pending-сесія та повідомлення з sent=0.

Вставити скриншот 7 — HTTP 202 і pending навіть без брокера, в outbox є sent=0; relay повторює спроби.

## Відновлення доставки

```powershell
docker compose start rabbitmq
sleep 10
curl.exe "http://localhost:8080/sessions/$offline"
curl.exe http://localhost:8080/debug/state
curl.exe http://localhost:8080/reports/5
```

Після готовності брокера стан стає completed, повідомлення отримує sent=1, а звіт збільшується на 75 секунд. За потреби зачекайте довше й повторіть читання; новий POST не потрібен.

Вставити скриншот 8 — збережене повідомлення доставлено, outbox sent=1, сесія completed, звіт збільшено на 75.

## Повторне повідомлення

```powershell
docker compose exec reporting python
```

У Python-консолі повторно передайте вже оброблену подію:

```python
import json
import storage
event = json.loads(storage.rows("SELECT body FROM outbox LIMIT 1")[0]["body"])
storage.rows("SELECT count(*) AS n FROM projection")
storage.project(event)
storage.rows("SELECT count(*) AS n FROM projection")
exit()
```

Результат обробки — duplicate; кількість рядків до й після однакова.

Вставити скриншот 9 — result duplicate; кількість записів у проєкції не збільшилася.

## Автоматичні перевірки

```powershell
docker compose exec tracking python -m unittest discover -s tests -v
```

Тести виконуються всередині контейнера; Python на Windows для них не потрібний.

Вставити скриншот 10 — 6 перевірок успішні: атомарність outbox, rollback, inbox, failure outbox, ідемпотентна компенсація та rollback response outbox.

## Завершення

```powershell
docker compose down
```

Дані залишаються в томах. Для звичайної зупинки не додавайте -v.

