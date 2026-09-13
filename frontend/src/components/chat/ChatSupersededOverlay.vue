<script setup>
</script>

<template>
  <div class="chat-superseded-overlay">
    <div class="chat-superseded-card">
      <h2 class="chat-superseded-title">Chat spostata su un altro client</h2>
      <p class="chat-superseded-body">
        Questa conversazione è stata aperta su un'altra finestra o dispositivo, che ora
        ha il controllo del canale. Da qui non è più possibile inviare né ricevere messaggi.
      </p>
    </div>
  </div>
</template>

<style scoped>
.chat-superseded-overlay {
  position: absolute;
  top: var(--chat-header-height);
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 15;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 1rem;
  background: rgba(0, 0, 0, 0.55);
  pointer-events: all;
  overflow-y: auto;
}

.chat-superseded-card {
  flex-shrink: 0;
  width: 100%;
  max-width: 420px;
  box-sizing: border-box;
  background: white;
  border: 1px solid #ddd;
  border-radius: 10px;
  box-shadow: 0 8px 30px rgba(0, 0, 0, 0.25);
  padding: 1.2rem 1.4rem;
}

.chat-superseded-title {
  margin: 0 0 0.5rem;
  font-size: 1.05rem;
  font-weight: 600;
  color: #333;
}

.chat-superseded-body {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.5;
  color: #444;
}
</style>
