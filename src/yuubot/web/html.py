from ..app import Yuubot


def html_page(app: Yuubot) -> str:
    actors = "\n".join(f'<option value="{actor_id}">{actor_id}</option>' for actor_id in app.actors)
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>yuubot</title>
<body>
<main>
  <pre id="bootstrap"></pre>
  <select id="integration-type"></select>
  <input id="integration-name" placeholder="integration name">
  <textarea id="integration-config" rows="6" placeholder='{{"access_token":"..."}}'></textarea>
  <button id="save-integration">Save integration</button>
  <textarea id="actor-config" rows="8" placeholder='{{"name":"Amy","model":"intelligent","tools":{{}}}}'></textarea>
  <button id="save-actor">Save actor</button>
  <select id="actor">{actors}</select>
  <input id="conversation" placeholder="conversation id">
  <textarea id="message" rows="4"></textarea>
  <button id="send">Send</button>
  <button id="stop">Stop</button>
  <pre id="log"></pre>
</main>
<script>
const log = document.getElementById('log');
const bootstrapEl = document.getElementById('bootstrap');

async function refreshBootstrap() {{
  const response = await fetch('/api/bootstrap');
  const bootstrap = await response.json();
  bootstrapEl.textContent = JSON.stringify(bootstrap, null, 2);
  const types = document.getElementById('integration-type');
  types.replaceChildren();
  bootstrap.integrations.forEach((integration) => {{
    const option = document.createElement('option');
    option.value = integration.type;
    option.textContent = integration.type;
    types.appendChild(option);
  }});
}}

refreshBootstrap();

document.getElementById('save-integration').onclick = async () => {{
  const type = document.getElementById('integration-type').value;
  const name = document.getElementById('integration-name').value || type;
  const config = JSON.parse(document.getElementById('integration-config').value || '{{}}');
  const response = await fetch(`/api/integrations/${{type}}/config`, {{
    method: 'PUT',
    headers: {{'content-type': 'application/json'}},
    body: JSON.stringify({{name, config}})
  }});
  bootstrapEl.textContent = JSON.stringify(await response.json(), null, 2);
}};

document.getElementById('save-actor').onclick = async () => {{
  const actor = JSON.parse(document.getElementById('actor-config').value || '{{}}');
  const actorId = actor.id || document.getElementById('actor').value;
  const response = await fetch(`/api/actors/${{encodeURIComponent(actorId)}}`, {{
    method: 'PUT',
    headers: {{'content-type': 'application/json'}},
    body: JSON.stringify(actor)
  }});
  bootstrapEl.textContent = JSON.stringify(await response.json(), null, 2);
}};

let socket = null;

document.getElementById('send').onclick = () => {{
  const actorId = document.getElementById('actor').value;
  const message = document.getElementById('message').value;
  const conversationId = document.getElementById('conversation').value;
  log.textContent = '';
  socket = new WebSocket(`ws://${{location.host}}/api/ws`);
  socket.onmessage = (event) => {{
    const frame = JSON.parse(event.data);
    if (frame.type === 'conversation.delta' && frame.payload.chunk.kind === 'text_delta') {{
      log.textContent += frame.payload.chunk.payload.text || '';
    }}
    if (frame.type === 'conversation.commit' && !frame.payload.continues) {{
      socket.close();
    }}
  }};
  socket.onopen = () => {{
    socket.send(JSON.stringify({{
      id: 'demo-send',
      type: 'conversation.send',
      payload: {{
        actor_id: actorId,
        conversation_id: conversationId || undefined,
        content: [{{kind: 'text', text: message, mime: 'text/plain'}}]
      }}
    }}));
  }};
}};

document.getElementById('stop').onclick = () => {{
  const conversationId = document.getElementById('conversation').value;
  if (!conversationId) return;
  if (!socket || socket.readyState !== WebSocket.OPEN) {{
    socket = new WebSocket(`ws://${{location.host}}/api/ws`);
    socket.onopen = () => {{
      socket.send(JSON.stringify({{
        id: 'demo-stop',
        type: 'conversation.interrupt',
        payload: {{conversation_id: conversationId}}
      }}));
    }};
    return;
  }}
  socket.send(JSON.stringify({{
    id: 'demo-stop',
    type: 'conversation.interrupt',
    payload: {{conversation_id: conversationId}}
  }}));
}};
</script>
</body>
</html>
"""
