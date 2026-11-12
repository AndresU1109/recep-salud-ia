const nombres = { base_de_datos: "PostgreSQL + pgvector", ollama: "Ollama (LLM y embeddings)" };
const lista = document.getElementById("estado");

fetch("/api/v1/health/dependencias")
  .then((r) => r.json())
  .then((d) => {
    lista.replaceChildren();
    for (const k of Object.keys(nombres)) {
      const li = document.createElement("li");
      li.className = d[k].estado;
      li.textContent = `${nombres[k]}: ${d[k].estado}${d[k].detalle ? " — " + d[k].detalle : ""}`;
      lista.append(li);
    }
  })
  .catch(() => {
    const li = document.createElement("li");
    li.className = "error";
    li.textContent = "No se pudo consultar la API.";
    lista.replaceChildren(li);
  });
