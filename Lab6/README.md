# Лабораторна робота 6 — виконання

Команди виконуються у PowerShell. `irm` — скорочення Invoke-RestMethod, `curl.exe` запускає саме curl, а не однойменний псевдонім PowerShell. Після створення сесії її відповідь зберігається у змінній, щоб не переписувати UUID вручну.

Перед запуском відкрийте Docker Desktop. Іншу лабораторну на порту 8080 спочатку зупиніть через `docker compose down` у її папці. Збережені дані залишаються між запусками: порівнюйте зміну підсумку, а не очікуйте порожню базу.

Знімки робіть після відповідних перевірок. Якщо результат ще pending, зачекайте й повторіть лише читання його стану. Повторний POST створює іншу сесію.

## 1. Запуск

```powershell
cd "C:\Users\Oleksii\Desktop\Masters\5th_year\SOA\Lab6"
docker version
docker compose config --quiet
docker compose up -d --build
docker compose ps
curl.exe http://localhost:8080/health
```

Дочекайтеся готовності контейнерів. Мережа стенда використовує підмережу 10.205.6.0/24. Зробіть скриншот 1.

## 2. Адреси

| Призначення | Адреса / доступ |
|---|---|
| Координатор API | [localhost:8080](http://localhost:8080/health) |
| RabbitMQ Management | [localhost:15672](http://localhost:15672), `student` / `student` |
| Prometheus | [localhost:9090](http://localhost:9090) |
| Prometheus targets | [localhost:9090/targets](http://localhost:9090/targets) |
| Правила та стани alerts | [localhost:9090/alerts](http://localhost:9090/alerts) |
| Grafana dashboard | [localhost:3000/d/soa-time](http://localhost:3000/d/soa-time), `admin` / `admin-lab6` |
| Alertmanager | [localhost:9093](http://localhost:9093) |
| n8n | [localhost:5678](http://localhost:5678) |
| Локальні incident tickets | [localhost:8090/incidents](http://localhost:8090/incidents) |

Облікові дані Grafana/RabbitMQ є локальними демонстраційними значеннями з compose. Якщо volume Grafana вже містить змінений пароль, використайте актуальний пароль цього volume.

## 3. Активувати workflow n8n — до ін’єкції відмов

1. Відкрийте [n8n](http://localhost:5678). На першому запуску пройдіть налаштування локального owner-акаунта: введіть власні ім’я, email і пароль. Зовнішній n8n Cloud-акаунт не потрібний. Надалі входьте у створений локальний акаунт.
2. Створіть workflow. У меню `…` редактора виберіть **Import from File**.
3. Виберіть файл `C:\Users\Oleksii\Desktop\Masters\5th_year\SOA\Lab6\monitoring\n8n-workflow.json`.
4. Переконайтеся, що видно два з’єднані вузли: `Alertmanager webhook` → `Create or update incident`.
5. У першому вузлі метод — `POST`, path — `soa-alert`. У другому — `POST http://incidents:8000/incidents`, JSON body — `={{ JSON.stringify($json.body) }}`.
6. Натисніть **Save**, потім переведіть workflow у **Active**. Зафіксована версія n8n — `1.82.3`; для неї використовується Activate/Active. У новіших n8n 2.x ця операція може називатися Publish, але compose тут не використовує 2.x.
7. Production URL: `http://localhost:5678/webhook/soa-alert`. Не підставляйте `/webhook-test/soa-alert`: тестовий URL залежить від ручного прослуховування і не замінює активований workflow.
8. Перейдіть у **Executions** для перегляду реальних виконань після наступних кроків.

Alertmanager усередині Docker використовує `http://n8n:5678/webhook/soa-alert`. HTTP Request node використовує Docker DNS `incidents`, а не `localhost:8090`: усередині контейнера localhost означає цей контейнер.

## 4. Повторні сповіщення

У monitoring/probe-firing.json та monitoring/probe-resolved.json лежать два приклади повідомлень із тим самим fingerprint=manual-probe. Це синтетичний тест каналу, а не відмова вузла.

```powershell
curl.exe http://localhost:5678/webhook/soa-alert -H "Content-Type: application/json" --data-binary "@monitoring/probe-firing.json"
curl.exe http://localhost:5678/webhook/soa-alert -H "Content-Type: application/json" --data-binary "@monitoring/probe-firing.json"
sleep 3
curl.exe http://localhost:8090/incidents
curl.exe http://localhost:5678/webhook/soa-alert -H "Content-Type: application/json" --data-binary "@monitoring/probe-resolved.json"
sleep 3
curl.exe http://localhost:8090/incidents
```

Порівняйте запис manual-probe: повтор не створює другого ID, а останнє повідомлення змінює статус на resolved. Якщо він залишився з попереднього запуску, перше повідомлення оновлює його до firing. Реальні виконання видно у n8n → Executions. Скриншот 9.

## 5. Сесії та спільний ідентифікатор

```powershell
curl.exe http://localhost:8080/users/5
curl.exe http://localhost:8080/users/6
curl.exe http://localhost:8080/reports/5
curl.exe http://localhost:8080/reports/6
$s5 = irm http://localhost:8080/sessions -Method Post -ContentType application/json -Headers @{'x-trace-id'='55555555555555555555555555555555'} -Body '{"user_id":5,"duration_seconds":1500}'
$s6 = irm http://localhost:8080/sessions -Method Post -ContentType application/json -Body '{"user_id":6,"duration_seconds":900}'
$s5
$s6
sleep 5
curl.exe "http://localhost:8080/sessions/$($s5.id)"
curl.exe "http://localhost:8080/sessions/$($s6.id)"
curl.exe http://localhost:8080/reports/5
curl.exe http://localhost:8080/reports/6
```

Після completed прирости мають становити 1500 і 900 секунд. Правило user_id % 2 направляє користувача 5 до shard 1, користувача 6 — до shard 0. Якщо ще pending, повторіть читання. Скриншот 2.

```powershell
docker compose logs --since 5m gateway tracking profiles reporting1 replica1 | findstr 55555555555555555555555555555555
```

Той самий trace ID має бути у записах кількох сервісів. Скриншот 3. Jaeger/OTLP у стенді немає: досліджується кореляція структурованих журналів.

## 6. Метрики

Коротка серія звернень створює дані для графіків:

```powershell
1..30 | % {
    curl.exe -s http://localhost:8080/reports/5 > $null
    curl.exe -s http://localhost:8080/reports/6 > $null
}
```

У Prometheus відкрийте /targets: мають бути доступні сім прикладних цілей і collector. Скриншот 4.

У Grafana відкрийте SOA Time Tracker, Last 15 minutes, оновлення 5s. Здоров’я вузлів, інтенсивність звернень і помилки — скриншот 5. Розподіл записів, replication lag, CPU та memory — скриншот 6. Панелі p50/p95/p99 — скриншот 7. Панель завантажується з provisioning автоматично.

Для точного значення відкрийте сторінку запитів Prometheus, вставте потрібний вираз і натисніть Execute. Кожен рядок — окремий запит:

```promql
up
shard_sessions
replication_lag_seconds
cluster_node_up
sum by (instance) (rate(http_requests_total[1m]))
histogram_quantile(0.50, sum by (le, instance) (rate(http_duration_seconds_bucket[1m])))
histogram_quantile(0.95, sum by (le, instance) (rate(http_duration_seconds_bucket[1m])))
histogram_quantile(0.99, sum by (le, instance) (rate(http_duration_seconds_bucket[1m])))
```

Порожнє значення без спостережень не означає нульову затримку. shard_sessions показує кількість записів, а не секунди роботи.

## 7. Недоступність reporting0

```powershell
docker compose stop reporting0
sleep 45
curl.exe -i http://localhost:8080/reports/5
curl.exe -i http://localhost:8080/reports/6
curl.exe http://localhost:8090/incidents
```

У Prometheus Alerts перевірте NodeDown у firing, а в запитах — up для reporting0:8000. Якщо стан ще pending, зачекайте й оновіть сторінку. Користувач 5 залишається на shard 1, а читання користувача 6 завершується помилкою: перемикання на replica не реалізоване. Скриншот 10.

У n8n → Executions перевірте успішне виконання та запис NodeDown у /incidents. Скриншот 11.

```powershell
docker compose start reporting0
sleep 45
curl.exe http://localhost:8080/reports/6
curl.exe http://localhost:8090/incidents
```

Той самий інцидент має стати resolved; ID та fingerprint не змінюються. Відновлення вузла тут ручне. Скриншот 12.

## 8. Відставання replica1

Внутрішні технічні адреси доступні лише з контейнерної мережі. Відкрийте Python у gateway:

```powershell
docker compose exec gateway python
```

```python
import httpx
httpx.post("http://replica1:8000/admin/delay/20").json()
exit()
```

Після exit() ви знову у PowerShell:

```powershell
1..3 | % { irm http://localhost:8080/sessions -Method Post -ContentType application/json -Body '{"user_id":5,"duration_seconds":60}' }
sleep 15
```

У Prometheus виконайте replication_lag_seconds. Для порівняння ідентифікаторів знову відкрийте Python у gateway:

```powershell
docker compose exec gateway python
```

```python
import httpx
httpx.get("http://reporting1:8000/debug/state").json()["projection"]
httpx.get("http://replica1:8000/debug/state").json()["projection"]
exit()
```

Множини ID мають відрізнятися, а відставання — перевищити 5 секунд. Скриншот 13. Порівняння під час затримки потрібно виконати до того, як replica наздожене основний вузол. Це вік відсутньої події, а не підставлене значення 20.

У Prometheus Alerts знайдіть ReplicationLag у firing, у n8n — виконання, у /incidents — відповідний запис. Скриншот 14.

```powershell
docker compose exec gateway python
```

```python
import httpx
httpx.post("http://replica1:8000/admin/delay/0").json()
exit()
```

```powershell
sleep 30
curl.exe http://localhost:8090/incidents
```

У Prometheus перевірте повернення lag до 0 та доступність обох копій. Уже розпочата пауза завершується сама, тому за потреби зачекайте довше. Інцидент має стати resolved. Скриншот 15.

## 9. Повільні відповіді

```powershell
docker compose exec gateway python
```

```python
import httpx
httpx.post("http://reporting1:8000/admin/http-delay/2").json()
exit()
```

```powershell
1..30 | % { curl.exe -s http://localhost:8080/reports/5 > $null }
curl.exe http://localhost:8090/incidents
```

Цикл триває близько хвилини. У Grafana має зрости p99; у Prometheus Alerts — HighLatency у firing. Скриншот 16. Оцінка гістограми не зобов’язана дорівнювати рівно двом секундам.

```powershell
docker compose exec gateway python
```

```python
import httpx
httpx.post("http://reporting1:8000/admin/http-delay/0").json()
exit()
```

```powershell
1..30 | % { curl.exe -s http://localhost:8080/reports/5 > $null }
sleep 90
curl.exe http://localhost:8090/incidents
```

Після виходу повільних запитів із хвилинного вікна інцидент переходить у resolved. Якщо він ще firing, оновіть графік і перевірте пізніше. Скриншот 17.

## 10. Ручні скриншоти

Використовуйте `Win+Shift+S` і вставляйте реальні знімки у відповідні місця протоколу. Не записуйте паролі owner-акаунта n8n на знімку.

| № | Що показати |
|---|---|
| 1 | перевірка compose-конфігурації та перелік запущених сервісів. |
| 2 | профілі 5/6, сесії completed, маршрути shard 1/0 і фактичні прирости звітів. |
| 3 | один trace ID у логах Gateway, Tracking, Profiles і primary/replica Reporting. |
| 4 | Prometheus Targets із Gateway, shards, replicas та collector. |
| 5 | Grafana: cluster health, throughput та error rate. |
| 6 | фактичні shard_sessions, replication_lag_seconds, CPU та memory. |
| 7 | Grafana: p50, p95 і p99 під фактичним навантаженням. |
| 8 | імпортований workflow з двома вузлами та станом Active. |
| 9 | ManualProbe: один ID після повтору та resolved для того самого fingerprint. |
| 10 | NodeDown firing, up=0 і поведінка звітів користувачів 5/6. |
| 11 | успішне виконання n8n та створений NodeDown ticket. |
| 12 | reporting0 відновлено, той самий ticket має status resolved. |
| 13 | IDs primary/replica1 розрізняються та lag перевищує 5 секунд. |
| 14 | ReplicationLag firing, виконання n8n і відповідний ticket. |
| 15 | replica наздогнала primary, lag=0 і ticket resolved. |
| 16 | p99 перевищує 1 секунду та HighLatency firing. |
| 17 | автоматичний HighLatency ticket і resolved після скидання затримки. |

## 11. Якщо щось не працює

```powershell
docker compose logs --tail 30 gateway tracking reporting0 reporting1 replica0 replica1 collector
docker compose logs --tail 30 prometheus alertmanager n8n incidents
curl.exe http://localhost:9090/api/v1/alerts
curl.exe http://localhost:9093/api/v2/alerts
curl.exe http://localhost:8090/incidents
```

Якщо n8n повертає 404, перевірте Active і production URL /webhook/soa-alert. Якщо запит прийнято, але інциденту немає, відкрийте другий вузол у Executions. Після виправлення повторіть ManualProbe. Порожні графіки на початку потребують звернень і кількох циклів збору метрик, а не вигаданих значень.

## 12. Завершення

Після скасування обох затримок, відновлення reporting0 та отримання resolved:

```powershell
docker compose ps
curl.exe http://localhost:8090/incidents
docker compose down
```

Команда down зберігає томи. Для звичайної зупинки не додавайте -v.
