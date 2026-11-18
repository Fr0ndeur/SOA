# Лабораторна робота 2 — виконання

Команди виконуються у PowerShell. `irm` — скорочення Invoke-RestMethod, `curl.exe` запускає саме curl, а не однойменний псевдонім PowerShell. Після створення сесії її відповідь зберігається у змінній, щоб не переписувати UUID вручну.

Перед запуском відкрийте Docker Desktop. Іншу лабораторну на порту 8080 спочатку зупиніть через `docker compose down` у її папці. Збережені дані залишаються між запусками: порівнюйте зміну підсумку, а не очікуйте порожню базу.

Знімки робіть після відповідних перевірок. Якщо результат ще pending, зачекайте й повторіть лише читання його стану. Повторний POST створює іншу сесію.

## Запуск

```powershell
cd "C:\Users\Oleksii\Desktop\Masters\5th_year\SOA\Lab2"
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

## Масштабування

```powershell
docker compose up -d --scale tracking=3 --no-build
docker compose ps tracking
1..9 | % { (irm "http://localhost:8080/sessions/$($session.id)").instance }
docker compose exec gateway getent hosts tracking
```

У відповідях мають повторюватися три різні ідентифікатори екземплярів. Команда getent показує адреси сервісу в мережі контейнерів.

Вставити скриншот 5 — три різні instance у відповідях та три IP-адреси DNS Tracking.

## Некоректні запити

```powershell
'{"user_id":5,"duration_seconds":-1}' | curl.exe -i http://localhost:8080/sessions -H "Content-Type: application/json" -d "@-"
'{"user_id":999,"duration_seconds":60}' | curl.exe -i http://localhost:8080/sessions -H "Content-Type: application/json" -d "@-"
```

Перший запит має повернути 422, другий — 404. JSON передається через стандартний ввід, тому PowerShell не змінює лапки в аргументах curl.

Вставити скриншот 6 — від’ємний час відхилено HTTP 422; невідомого користувача відхилено HTTP 404.

## Черга при зупиненій звітності

```powershell
docker compose stop reporting
$queued = irm http://localhost:8080/sessions -Method Post -ContentType application/json -Body '{"user_id":5,"duration_seconds":600}'
$queued
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready consumers
```

У reports залишається повідомлення без обробника. Не очищайте вікно перед наступними командами.

## Відновлення звітності

```powershell
docker compose start reporting
sleep 5
curl.exe "http://localhost:8080/sessions/$($queued.id)"
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready consumers
```

Після відновлення повідомлення споживається, сесія стає completed. Якщо обробка ще не завершена, повторіть GET і перегляд черги.

Вставити скриншот 7 — Черга до та після відновлення Reporting: повідомлення очікує при зупиненому обробнику, після запуску спожите, сесія completed. Показати обидва результати послідовно в одному вікні термінала.

## Завершення

```powershell
docker compose up -d --scale tracking=1 --no-build
docker compose down
```

Команда down зберігає томи з даними. Для звичайної зупинки не додавайте -v.

