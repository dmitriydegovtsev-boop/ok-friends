"""Макет страниц ОК для сухого прогона быстрого режима (та же разметка, что у ok.ru на 20–27.10.2026).
Запуск отдельно: python tools/mock_ok.py 8765. Из tools/test_fast.py поднимается сам.

Заявки: kind = ok | modal_max (окно «у пользователя максимальное количество друзей») | card_max (то же
надписью на карточке) | error («Ошибка принятия заявки») | silent (ОК молчит).
Друзья: kind = ok | silent (окно подтверждения не закрывается) | nomenu (меню не всплывает).
Управление: /ctl?reset=1&captcha=1&cert=1&silent_all=1&own_limit=1 (0 — выключить), а также (v0.2.1):
  nreq=, nfr=, fr_page= — сколько заявок/друзей при reset и сколько друзей на странице;
  toast=N — каждые N успешных действий всплывашка «<Имя> хочет с вами подружиться» с «Принять» и крестиком
           поверх верхних карточек; по очереди разметкой ОК (#NotificationsGrowl .growl) и незнакомой;
  rec=1 — блок «Возможно, вы знакомы» с «Добавить в друзья» над списком друзей; rec=2 — плюс такая же
          карточка внутри контейнера списка;
  header=1 — высокая закреплённая шапка, верхний ряд карточек частично под ней;
  fr_base=N / fr_text=… — свой счётчик «Все» (число = fr_base + друзья + принятые; fr_text — текст как есть);
  limit_after=N — после N принятых каждое «Принять» отвечает окном «максимальное количество друзей»;
  stale_after=N — после N успешных действий на одной загрузке страницы меню друга перестаёт всплывать, а
                  «Принять» перестаёт работать (как у друга 26.10), перезагрузка страницы лечит;
  nomenu_all=1 — меню не всплывает ни у кого.
Как на настоящем ОК (24.10.2026): рекламная плашка #hook_Block_FixedPromoBanner внизу слева поверх карточек;
кнопки начинают работать только после полной загрузки (картинка /slow.png отдаётся через 1,5 с); после
«Прекратить» вместо окна подтверждения — окно «Этот человек удалён из списка ваших друзей» с «Закрыть»;
закреплённая шапка #topPanel высотой 48 px."""
import html
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PAGE = 20          # заявок на странице, дальше — догрузка прокруткой

STYLE = """<style>
body{font-family:sans-serif;margin:0;padding:%dpx 20px 20px}
#topPanel{position:fixed;left:0;top:0;right:0;height:%dpx;background:#eee;border-bottom:1px solid #ccc;z-index:2601}
.js-entity-item,.user-grid-card{border:1px solid #ccc;margin:6px;padding:8px;width:520px;position:relative}
.user-grid-card{display:inline-block;width:150px;vertical-align:top}
.user-grid-card_img{width:120px;height:120px;background:#c9d6e3}
.sc-menu{position:absolute;left:-10px;top:110px;width:160px;height:240px;background:#fff;border:1px solid #888;padding:6px;z-index:5}
.sc-menu a{position:absolute;bottom:8px}
.backdrop{position:fixed;left:0;top:0;right:0;bottom:0;background:rgba(0,0,0,.3);z-index:9}
.fixed_promo_banner_promo-banner{position:fixed;left:20px;bottom:20px;width:382px;height:178px;z-index:4000;background:#f60}
.sc-menu__hidden{display:none}
.sc-menu__top{top:-250px}
.__error{color:#c00}
.nav_menu_tab{margin-right:12px}
#hook_Modal_popLayerModal,.modal-new{position:fixed;left:30%%;top:30%%;background:#fff;border:2px solid #333;padding:20px;z-index:10}
.growl,.ntf-popup{position:fixed;left:10px;top:52px;width:420px;height:140px;z-index:3000;background:#fffbe6;border:1px solid #999}
</style>"""

INT_KEYS = ("toast", "rec", "header", "fr_base", "limit_after", "stale_after", "nomenu_all", "fr_page")
FLAG_KEYS = ("captcha", "cert", "silent_all", "own_limit")


def default_state(nreq=45, nfr=30):
    reqs = []
    for i in range(1, nreq + 1):
        kind = {5: "modal_max", 12: "card_max", 18: "silent", 27: "error"}.get(i, "ok")
        reqs.append({"id": str(1000 + i), "name": f"Заявитель {i}", "kind": kind, "done": False})
    friends = []
    for i in range(1, nfr + 1):
        kind = {4: "silent", 9: "nomenu"}.get(i, "ok")
        friends.append({"id": str(2000 + i), "name": f"Друг {i}", "kind": kind, "gone": False})
    st = {"reqs": reqs, "friends": friends, "accept_log": [], "unfriend_log": [], "fr_text": "",
          "actions": 0, "toast_at": 0, "toast_shown": 0, "toast_closed": 0, "toast_accept": 0, "rec_add": 0,
          "loads": {"friends": 0, "requests": 0}}
    st.update({k: False for k in FLAG_KEYS})
    st.update({k: 0 for k in INT_KEYS})
    st["fr_page"] = 25
    return st


STATE = default_state()
LOCK = threading.Lock()


def own_counter():
    if STATE["fr_text"]:
        return STATE["fr_text"]
    n = STATE["fr_base"] + sum(1 for f in STATE["friends"] if not f["gone"]) + sum(1 for r in STATE["reqs"] if r["done"])
    return f"{n:,}".replace(",", "\xa0")


def nav():
    # как на ok.ru 27.10.2026: вкладки «Все», «На сайте» (обе data-l=t,userFriend), «Заявки»
    return ("<div id='topPanel'>ОК</div><div id='hook_Block_Navigation' class='nav_menu_tabs'>"
            "<a class='nav_menu_tab' data-l='t,userFriend' role='tab' href='/profile/1/friends'><div class='nav_menu_tab-label'>Все</div>"
            f"<span class='nav_menu_tab-counter'>{own_counter()}</span></a>"
            "<a class='nav_menu_tab' data-l='t,userFriend' role='tab' href='/profile/1/friends?online=on'><div class='nav_menu_tab-label'>На сайте</div>"
            "<span class='nav_menu_tab-counter'>3</span></a>"
            "<a class='nav_menu_tab' data-l='t,userFriendRequest' role='tab' href='/profile/1/friendRequests'>Заявки "
            f"<span class='nav_menu_tab-counter'>{sum(1 for r in STATE['reqs'] if not r['done'])}</span></a></div>")


def req_cards(items):
    return "".join(
        f"<div class='js-entity-item' data-entity-id='{r['id']}'><div class='ucard-w_t'><a class='o' href='/profile/{r['id']}'>"
        f"{r['name']}</a></div><div class='entity-item_status'></div>"
        f"<button class='js-entity-accept'>Принять</button><button class='js-entity-accept' style='display:none'>Принять</button></div>"
        for r in items)


def fr_cards(items):
    return "".join(
        f"<div class='user-grid-card' data-id='{f['id']}' data-kind='{f['kind']}'><div class='user-grid-card_img'></div>"
        f"<div class='user-grid-card_name'><a href='/profile/{f['id']}'>{f['name']}</a></div>"
        f"<div class='sc-menu sc-menu__hidden'><div>{f['name']}</div><a class='u-menu_a' aria-label='Прекратить дружбу' href='#'>Прекратить дружбу</a></div></div>"
        for f in items)


def rec_cards(n, start=1):
    # «Возможно, вы знакомы»: та же карточка, что у друга, но с «Добавить в друзья» и без меню
    return "".join(
        f"<div class='user-grid-card' data-id='{3000 + i}' data-kind='rec'><div class='user-grid-card_img'></div>"
        f"<div class='user-grid-card_name'><a href='/profile/{3000 + i}'>Рекомендация {i}</a></div>"
        f"<button class='rec-add'>Добавить в друзья</button></div>"
        for i in range(start, start + n))


# всплывашки ОК: опрос каждые 0,6 с, «Принять»/«Дружить» в них и крестик считаются на сервере
TOAST_JS = """<script>
window.addEventListener('load', () => setInterval(async () => {
  const r = await (await fetch('/api/toast')).json();
  if (!r.show) return;
  document.querySelectorAll('#NotificationsGrowl, .ntf-popup').forEach(e => e.remove());
  const html = r.kind === 'growl'
    ? '<div id="NotificationsGrowl" class="h-mod"><div id="hook_Block_NotificationsGrowl_Content"><div class="growl __active">' +
      '<div class="growl_cnt"><div class="growl_tx"><div class="growl_name">' + r.name + '</div> хочет с\\u00a0вами подружиться ' +
      '<a class="al growl_open toast-accept">Принять</a></div></div><span class="ic growl_close" title="скрыть">×</span></div></div></div>'
    : '<div class="ntf-popup"><b>' + r.name + '</b> хочет с\\u00a0вами подружиться <button class="toast-accept">Принять</button>' +
      '<span class="ntf-x">×</span></div>';
  document.body.insertAdjacentHTML('beforeend', html);
}, 600));
document.addEventListener('click', ev => {
  if (ev.target.closest('.toast-accept')) { ev.preventDefault(); fetch('/api/toast_accept', {method: 'POST'}); }
  const x = ev.target.closest('.growl_close, .ntf-x');
  if (x) { fetch('/api/toast_close', {method: 'POST'}); (x.closest('#NotificationsGrowl') || x.closest('.ntf-popup')).remove(); }
  if (ev.target.closest('.rec-add')) fetch('/api/rec_add', {method: 'POST'});
}, true);
window.okDone = 0;
</script>"""

REQ_JS = """<script>
let offset = %d;
const STALE = %d;
window.addEventListener('load', () => document.addEventListener('click', async ev => {
  const b = ev.target.closest('button.js-entity-accept');
  if (b) {
    if (STALE && window.okDone >= STALE) return;   // страница «залипла»: кнопка не отвечает до перезагрузки
    const card = b.closest('.js-entity-item'), id = card.dataset.entityId;
    const r = await (await fetch('/api/accept?id=' + id, {method: 'POST'})).json();
    setTimeout(() => {
      if (r.kind === 'ok') { window.okDone++; card.querySelectorAll('button').forEach(x => x.remove());
        card.insertAdjacentHTML('beforeend', '<span>Заявка\\u00a0принята</span>'); }
      else if (r.kind === 'card_max' || r.kind === 'error') { const st = card.querySelector('.entity-item_status');
        st.classList.add('__error');
        st.textContent = r.kind === 'error' ? 'Ошибка принятия заявки' : 'У\\u00a0пользователя максимальное количество друзей'; }
      else if (r.kind === 'modal_max' || r.kind === 'own_limit' || r.kind === 'max_amb') {
        const t = r.kind === 'own_limit' ? 'Вы достигли максимального количества друзей'
          : r.kind === 'max_amb' ? 'Невозможно принять заявку. Максимальное количество\\u00a0друзей — 6000'
          : 'Невозможно принять заявку. У\\u00a0пользователя\\n максимальное  количество друзей';
        document.body.insertAdjacentHTML('beforeend', '<div class="modal-new" role="dialog"><div>' + t + '</div><a class="modal-new_close" aria-label="Закрыть" href="#">×</a></div>'); }
    }, 300);
  }
  const c = ev.target.closest('.modal-new_close');
  if (c) { ev.preventDefault(); c.closest('.modal-new').remove(); }
}));
window.addEventListener('scroll', async () => {
  if (window.innerHeight + window.scrollY < document.body.scrollHeight - 50 || window.loading) return;
  window.loading = true;
  const html = await (await fetch('/api/reqs?offset=' + offset)).text();
  if (html) { document.querySelector('#hook_Block_UserFriendRequestMRB').insertAdjacentHTML('beforeend', html); offset += %d; }
  window.loading = false;
});
</script>"""

FR_JS = """<script>
let offset = %d;
const STALE = %d, NOMENU = %d;
document.addEventListener('mouseover', ev => {
  const img = ev.target.closest('.user-grid-card_img'); if (!img) return;
  const card = img.closest('.user-grid-card'); if (card.dataset.kind === 'nomenu' || NOMENU) return;
  if (STALE && window.okDone >= STALE) return;   // страница «залипла»: меню не всплывает до перезагрузки
  const m = card.querySelector('.sc-menu'); if (!m) return;
  document.querySelectorAll('.sc-menu').forEach(m => m.classList.add('sc-menu__hidden'));
  // как у ОК: внизу мало места — меню открывается вверх
  m.classList.toggle('sc-menu__top', img.getBoundingClientRect().bottom + 260 > window.innerHeight);
  m.classList.remove('sc-menu__hidden');
});
document.addEventListener('mouseout', ev => {
  const card = ev.target.closest('.user-grid-card'); if (!card || card.contains(ev.relatedTarget)) return;
  const m = card.querySelector('.sc-menu'); if (m) setTimeout(() => m.classList.add('sc-menu__hidden'), 300);
});
window.addEventListener('load', () => document.addEventListener('click', async ev => {
  const a = ev.target.closest('a.u-menu_a');
  if (a) { ev.preventDefault(); const card = a.closest('.user-grid-card');
    document.body.insertAdjacentHTML('beforeend', '<div class="backdrop"></div><div id="hook_Modal_popLayerModal"><div>Прекратить дружбу?</div>' +
      '<input type="hidden" id="field_userIds" value="' + card.dataset.id + '"><button id="hook_FormButton_button_delete_confirm">Прекратить</button></div>'); return; }
  const b = ev.target.closest('#hook_FormButton_button_delete_confirm');
  if (b) { const id = document.querySelector('#field_userIds').value;
    const r = await (await fetch('/api/unfriend?id=' + id, {method: 'POST'})).json();
    if (r.kind === 'ok') setTimeout(() => { window.okDone++; document.querySelector('#hook_Modal_popLayerModal').innerHTML =
      '<div>Прекратить дружбу</div><div>Этот человек удалён из списка ваших друзей.</div><button class="js-close">Закрыть</button>'; }, 300); }
  const c = ev.target.closest('.js-close');
  if (c) { document.querySelector('#hook_Modal_popLayerModal').remove(); document.querySelector('.backdrop').remove(); }
}));
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') { document.querySelectorAll('#hook_Modal_popLayerModal, .backdrop').forEach(m => m.remove()); } });
window.addEventListener('scroll', async () => {
  if (window.innerHeight + window.scrollY < document.body.scrollHeight - 50 || window.loading) return;
  window.loading = true;
  const html = await (await fetch('/api/friends?offset=' + offset)).text();
  if (html) { document.querySelector("[data-block='MyFriendsSquareCardsPagingB']").insertAdjacentHTML('beforeend', html); offset += %d; }
  window.loading = false;
});
</script>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body: str, ctype="text/html; charset=utf-8", code=200, headers=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        try:
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass        # страницу перезагрузили, пока макет отдавал медленную картинку

    def page(self, inner: str, extra: str = ""):
        banner = ("<div id='hook_Block_FixedPromoBanner'><article class='fixed_promo_banner_promo-banner'>Рецепты "
                  "<button>Скрыть</button></article></div><img src='/slow.png' width=1 height=1>")
        style = STYLE % ((20, 130) if STATE["header"] else (60, 48))
        self.send(f"<!doctype html><html><head><meta charset='utf-8'>{style}</head><body>{nav()}{inner}{banner}"
                  f"{TOAST_JS}{extra}</body></html>")

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        with LOCK:
            if u.path == "/ctl":
                global STATE
                if q.get("reset") == "1":
                    STATE = default_state(int(q.get("nreq", 45)), int(q.get("nfr", 30)))
                for k in FLAG_KEYS:
                    if k in q:
                        STATE[k] = q[k] == "1"
                for k in INT_KEYS:
                    if k in q:
                        STATE[k] = int(q[k])
                if "fr_text" in q:
                    STATE["fr_text"] = q["fr_text"]
                return self.send(json.dumps({k: STATE[k] for k in FLAG_KEYS + INT_KEYS + ("fr_text",)}, ensure_ascii=False),
                                 "application/json")
            if u.path == "/slow.png":
                LOCK.release()
                try:
                    time.sleep(1.5)
                finally:
                    LOCK.acquire()
                return self.send("", "image/png")
            if u.path == "/state":
                return self.send(json.dumps(STATE, ensure_ascii=False), "application/json")
            if u.path == "/api/toast":
                show = STATE["toast"] > 0 and STATE["actions"] - STATE["toast_at"] >= STATE["toast"]
                if not show:
                    return self.send('{"show": false}', "application/json")
                STATE["toast_at"] = STATE["actions"]
                STATE["toast_shown"] += 1
                kind = "growl" if STATE["toast_shown"] % 2 else "plain"
                name = html.escape(f"Новенький {STATE['toast_shown']}")
                return self.send(json.dumps({"show": True, "kind": kind, "name": name}, ensure_ascii=False), "application/json")
            if u.path in ("/", "/friends"):
                return self.page("<h1>Друзья</h1>")
            if STATE["cert"] and u.path.startswith("/profile/"):
                return self.send("", code=302, headers={"Location": "https://expired.badssl.com/"})
            if STATE["captcha"] and u.path.startswith("/profile/"):
                return self.page("<h2>Введите код с картинки</h2><img src='/captcha.png'>")
            pending = [r for r in STATE["reqs"] if not r["done"]]
            friends = [f for f in STATE["friends"] if not f["gone"]]
            fp = STATE["fr_page"]
            if u.path == "/profile/1/friendRequests":
                STATE["loads"]["requests"] += 1
                if not pending:
                    return self.page("<div>Пока нет входящих заявок</div>")
                return self.page(f"<div id='hook_Block_UserFriendRequestMRB'>{req_cards(pending[:PAGE])}</div>",
                                 REQ_JS % (PAGE, STATE["stale_after"], PAGE))
            if u.path == "/api/reqs":
                o = int(q.get("offset", 0))
                return self.send(req_cards(pending[o:o + PAGE]))
            if u.path == "/profile/1/friends":
                STATE["loads"]["friends"] += 1
                if not friends:
                    return self.page("<div>Пока нет друзей</div>")
                rec = ""
                if STATE["rec"]:
                    rec = ("<div id='hook_Block_PossibleFriendsHorizontal'><h3>Возможно, вы знакомы</h3>"
                           f"{rec_cards(4)}</div>")
                inside = rec_cards(1, 9) if STATE["rec"] == 2 else ""
                return self.page(f"{rec}<div data-block='MyFriendsSquareCardsPagingB'>{inside}{fr_cards(friends[:fp])}</div>",
                                 FR_JS % (fp, STATE["stale_after"], STATE["nomenu_all"], fp))
            if u.path == "/api/friends":
                o = int(q.get("offset", 0))
                return self.send(fr_cards(friends[o:o + fp]))
        self.send("not found", code=404)

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        with LOCK:
            for path, key in (("/api/toast_accept", "toast_accept"), ("/api/toast_close", "toast_closed"),
                              ("/api/rec_add", "rec_add")):
                if u.path == path:
                    STATE[key] += 1
                    return self.send("{}", "application/json")
            if u.path == "/api/accept":
                r = next((r for r in STATE["reqs"] if r["id"] == q.get("id")), None)
                accepted = sum(1 for x in STATE["reqs"] if x["done"])
                if STATE["silent_all"]:
                    kind = "silent"
                elif STATE["own_limit"]:
                    kind = "own_limit"
                elif STATE["limit_after"] and accepted >= STATE["limit_after"]:
                    kind = "max_amb"
                else:
                    kind = r["kind"] if r else "silent"
                STATE["accept_log"].append([round(time.time(), 3), q.get("id"), kind])
                if kind == "ok":
                    r["done"] = True
                    STATE["actions"] += 1
                return self.send(json.dumps({"kind": kind}), "application/json")
            if u.path == "/api/unfriend":
                f = next((f for f in STATE["friends"] if f["id"] == q.get("id")), None)
                kind = f["kind"] if f else "silent"
                STATE["unfriend_log"].append([round(time.time(), 3), q.get("id"), kind])
                if kind == "ok":
                    f["gone"] = True
                    STATE["actions"] += 1
                return self.send(json.dumps({"kind": kind}), "application/json")
        self.send("not found", code=404)


def serve(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
    print("макет ОК: http://127.0.0.1:%s" % (sys.argv[1] if len(sys.argv) > 1 else 8765))
    threading.Event().wait()
