(function () {
  var body = document.getElementById('rebook-body');
  if (!body) return;

  var summary = document.getElementById('rebook-summary');
  var refreshButton = document.getElementById('rebook-refresh');
  var demoButton = document.getElementById('rebook-demo');

  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch];
    });
  }

  function money(cents) {
    var value = Number(cents || 0) / 100;
    return '¥' + value.toFixed(2);
  }

  async function request(url, options) {
    var response = await fetch(url, Object.assign(
      { headers: { 'Content-Type': 'application/json' } }, options || {}
    ));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || data.code || ('HTTP ' + response.status));
    return data;
  }

  function notify(message, isError) {
    var notice = document.getElementById('notice');
    if (notice) {
      notice.textContent = message || '';
      notice.style.color = isError ? 'var(--bad, #b42318)' : '';
    }
    if (summary && message) summary.textContent = message;
  }

  var STATUS_LABELS = {
    ALLOCATED: '可抵扣 · 待确认',
    CONFIRMED: '已确认抵扣',
    HELD: '暂缓 · 未扣额',
    CANCELED: '已撤销 · 余额已释放',
    REJECTED: '申请无效'
  };

  var REASON_LABELS = {
    INSUFFICIENT_BALANCE: '可用余额不足，整单暂缓',
    INVALID_AMOUNT: '抵扣金额无效',
    CANCELED: '单据已撤销',
    TICKET_ID_REQUIRED: '缺少原票号'
  };

  function actionButtons(order) {
    var id = encodeURIComponent(order.order_id || order.id);
    if (order.status === 'ALLOCATED') {
      return '<button type="button" data-action="confirm" data-id="' + id + '">确认</button>' +
             '<button type="button" data-action="cancel" data-id="' + id + '">撤销</button>';
    }
    if (order.status === 'HELD') {
      return '<button type="button" data-action="retry" data-id="' + id + '">重试</button>';
    }
    if (order.status === 'CONFIRMED') {
      return '<button type="button" disabled>不可变更</button>';
    }
    return '<button type="button" disabled>已结束</button>';
  }

  function render(data) {
    var tickets = {};
    (data.tickets || []).forEach(function (ticket) { tickets[ticket.ticket_id] = ticket; });
    var orders = data.orders || data.results || [];
    if (summary) {
      var s = data.summary || {};
      summary.textContent = '共 ' + (s.order_count || orders.length) + ' 笔；已分配 ' +
        money(s.allocated_cents) + '；可用余额 ' + money(s.available_cents);
    }
    if (!orders.length) {
      body.innerHTML = '<tr><td class="empty" colspan="7">暂无改期单抵扣结果</td></tr>';
      return;
    }
    body.innerHTML = orders.map(function (order) {
      var ticket = tickets[order.ticket_id] || {};
      var dateText = [order.ticket_on || order.issue_date, order.due_on || order.deadline]
        .filter(Boolean).join(' / ');
      var reason = order.reason_code ? '<br><small>' + esc(REASON_LABELS[order.reason_code] || order.reason_code) + '</small>' : '';
      var balanceText = order.status === 'CANCELED'
        ? '退回 ' + money(order.refunded_cents)
        : '抵扣 ' + money(order.allocated_cents || order.deducted_cents);
      return '<tr data-order-id="' + esc(order.order_id) + '">' +
        '<td><strong>' + esc(order.order_id) + '</strong>' +
          (order.is_today ? '<br><span class="today-chip">今日出票优先</span>' : '') + '</td>' +
        '<td>' + esc(order.ticket_id) + '<br><small>可用 ' + money(ticket.available_cents) + '</small></td>' +
        '<td class="num">' + money(order.amount_cents) + '</td>' +
        '<td>' + esc(dateText || '-') + '</td>' +
        '<td><span class="verdict" data-v="' + esc(order.status === 'HELD' ? 'REVIEW' : order.status === 'REJECTED' ? 'REJECT' : 'PASS') + '">' +
          esc(STATUS_LABELS[order.status] || order.status) + '</span>' + reason + '</td>' +
        '<td class="num">' + balanceText + '</td>' +
        '<td><div class="row-actions">' + actionButtons(order) + '</div></td>' +
      '</tr>';
    }).join('');
  }

  async function load() {
    try {
      var data = await request('/api/rebooking/workbench');
      render(data);
    } catch (error) {
      notify(error.message, true);
    }
  }

  body.addEventListener('click', async function (event) {
    var button = event.target.closest('button[data-action]');
    if (!button) return;
    var action = button.getAttribute('data-action');
    var id = button.getAttribute('data-id');
    button.disabled = true;
    try {
      await request('/api/rebooking/orders/' + id + '/' + action, { method: 'POST' });
      notify(action === 'cancel' ? '撤销成功，余额已释放；重复请求不会再次退回。' : '操作完成');
      await load();
    } catch (error) {
      notify(error.message, true);
      button.disabled = false;
    }
  });

  refreshButton.addEventListener('click', load);
  demoButton.addEventListener('click', async function () {
    demoButton.disabled = true;
    try {
      var data = await request('/api/rebooking/demo/reset', { method: 'POST', body: '{}' });
      render(data);
      notify('已载入改期余额分配示例');
    } catch (error) {
      notify(error.message, true);
    } finally {
      demoButton.disabled = false;
    }
  });

  var form = document.getElementById('rebook-form');
  if (form) {
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      var formData = new FormData(form);
      var payload = {
        ticket_id: String(formData.get('ticket_id') || '').trim(),
        order_id: String(formData.get('order_id') || '').trim(),
        amount_cents: Number(formData.get('amount_cents') || 0),
        today: formData.get('today') === 'on'
      };
      var balance = Number(formData.get('balance_cents') || 0);
      if (balance > 0) payload.balance_cents = balance;
      try {
        await request('/api/rebooking/orders', { method: 'POST', body: JSON.stringify(payload) });
        form.querySelector('[name=order_id]').value = '';
        form.querySelector('[name=amount_cents]').value = '';
        notify('改期单已提交并完成余额分配');
        await load();
      } catch (error) {
        notify(error.message, true);
      }
    });
  }

  async function boot() {
    try {
      var data = await request('/api/rebooking/workbench');
      if (!(data.orders || []).length) {
        data = await request('/api/rebooking/demo', { method: 'POST', body: '{}' });
      }
      render(data);
    } catch (error) {
      notify(error.message, true);
    }
  }

  boot();
})();
