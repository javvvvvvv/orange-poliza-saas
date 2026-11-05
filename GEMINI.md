Estándar de código — Orange Crew + estilo personal

Esta skill define cómo debe escribirse, editase y revisarse código en cualquier lenguaje, para cualquier proyecto (web, scripts, ERP, revisiones de código). Se aplica por defecto, no solo cuando el usuario lo pide explícitamente.

0. Máximo poder de razonamiento, mínimo gasto de tokens

El objetivo es usar toda la capacidad de razonamiento de Claude en el trabajo pesado (arquitectura, casos borde, seguridad, elección de la mejor solución), pero que la respuesta final al usuario sea lo más ligera posible en tokens.

- Piensa internamente todo lo necesario (arquitectura, edge cases, trade-offs de seguridad y rendimiento) ANTES de escribir código, pero no narres ese proceso de pensamiento en la respuesta. La respuesta final va directo al código y a lo esencial.
- No repitas el checklist de la sección 10 en cada respuesta: es una guía interna para autoevaluarte antes de entregar, no un texto que deba aparecer en la salida a menos que el usuario pida explícitamente una revisión o auditoría.
- Nada de preámbulos ("claro, aquí tienes", "voy a crear..."), ni cierres genéricos. Ve directo al código.
- No repitas en prosa lo que el código ya dice. Si un comentario en el código ya explica algo, no lo vuelvas a explicar en texto aparte.
- Si el usuario pide varios archivos, entrégalos con mínima prosa entre ellos (una línea de contexto máximo, no un párrafo).
- Para snippets simples o de una sola función, no apliques toda la ceremonia de arquitectura por capas ni el bloque de licencia (ver regla de exclusión en la sección 9); resuelve directo.
- Aun ahorrando tokens, nunca sacrifiques corrección, seguridad ni los puntos obligatorios de las secciones 2, 5 y 9 cuando apliquen: son innegociables, la eficiencia es sobre la forma de comunicar, no sobre la calidad del código.
- El comando de git y el "siguiente paso" (sección 7 y 8) van en una sola línea cada uno, sin explicación adicional salvo que el usuario pregunte.

1. Estilo general
- Código con estilo humano, limpio y eficiente. Nada de patrones robóticos ni sobre-ingeniería innecesaria.
- Comentarios explicativos SOLO en partes complejas del código. No comentar lo obvio.
- El lenguaje depende del proyecto (no hay uno fijo); adapta las reglas de tipado estricto al lenguaje correspondiente.
- Cero emojis en código, comentarios, commits o explicaciones. Formato 100% técnico y profesional.
- Concisión absoluta: directo al grano, sin relleno.

2. Programación defensiva (cero bugs)
- Tipado estricto siempre: TypeScript en el ecosistema JS (cero any), Type Hints rigurosos en Python, o el equivalente estricto del lenguaje en uso.
- Nunca confiar en datos del frontend o del usuario: validar entradas en la capa de API/rutas con validadores (Zod, Pydantic, o el equivalente del lenguaje) antes de que los datos toquen el núcleo de negocio (core).
- Todo bloque que interactúe con bases de datos, APIs externas o cálculos críticos debe ir envuelto en try/catch o try/except. El sistema nunca debe colapsar sin control; debe manejar el error con gracia y devolver códigos HTTP correctos cuando aplique.

3. KISS, DRY y SOLID
- Responsabilidad única: una función hace una sola cosa. Si una función supera ~20 líneas, evalúa si conviene dividirla.
- Evita duplicación de lógica; extrae a funciones/módulos reutilizables.

4. Arquitectura limpia por capas

Todo proyecto nuevo (o módulo nuevo dentro de uno existente) debe organizarse en capas separadas:

- /api o /routes: enrutamiento HTTP puro y validación de datos de entrada.
- /core o /services: lógica de negocio pura, sin mezclar con HTTP ni UI.
- /models o /database: estructura de datos y acceso a la base de datos. Nunca mezclar consultas directas en la UI o en la capa de API.
- /ui o /components: exclusivo para frontend.

No mezcles responsabilidades entre capas (ej. no hagas queries SQL dentro de un componente de UI).

5. Seguridad y manejo de credenciales
- Zero Trust: nunca incluir credenciales, contraseñas o URLs de base de datos directamente en el código.
- Todo dato sensible se consume vía variables de entorno (.env).
- Al crear un proyecto o repositorio nuevo, genera siempre:
  - Un .gitignore estricto (incluyendo .env, node_modules, __pycache__, carpetas de build, etc. según el stack).
  - Un .env.example con las claves necesarias mostradas sin valores reales.

6. Documentación y trazabilidad legal (INDAUTOR México)
- Mantén (o crea si no existe) un archivo docs/MEMORIA_TECNICA.md con la descripción funcional y diagramas lógicos del proyecto, listo para eventual registro de software ante INDAUTOR México.
- Código modularizado de forma que sea fácil extraer el código fuente inicial y final para trazabilidad.
- Usa Docstrings/JSDoc (o el estándar del lenguaje) en las cabeceras de funciones complejas.

7. Control de versiones
- Después de cada bloque funcional entregado, proporciona el comando exacto de git usando Conventional Commits (ej. git commit -m "feat(core): agrega validación de pólizas").

8. Cierre proactivo de cada respuesta de código
- Al terminar de entregar un bloque de código, indica brevemente qué módulo sigue o cómo probar (unit testing) lo que se acaba de escribir.
- Si se reporta un bug, usa deducción arquitectónica: identifica en qué capa está probablemente la falla y pide al usuario que comparta EXCLUSIVAMENTE ese fragmento, en vez de pedir todo el proyecto.

9. Licencia comercial y firma corporativa (OBLIGATORIO, siempre)

Todo archivo principal, script ejecutable o núcleo de cualquier proyecto nuevo que generes —sin excepción, sea o no explícitamente un proyecto de Orange Crew— debe incluir en la cabecera este bloque, adaptado a la sintaxis de comentarios del lenguaje correspondiente:

/* ============================================================================
   PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
   ============================================================================
   Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
   Organización: ORANGE CREW
   Contacto: ILLANJAVIER9@GMAIL.COM

   ADVERTENCIA LEGAL (MÉXICO Y GLOBAL):
   Este código fuente y su arquitectura son propiedad intelectual exclusiva de
   JAVIER ILLAN GONZALEZ. Queda estrictamente prohibida su reproducción,
   distribución, modificación, ingeniería inversa, copia o uso comercial sin la
   autorización expresa y por escrito del autor. Obra protegida conforme a la
   Ley Federal del Derecho de Autor y tratados internacionales aplicables.
   ============================================================================ */

Para lenguajes sin comentarios de bloque /* */ (ej. Python, bash), usa el equivalente con # línea por línea manteniendo el mismo contenido.
No agregues este bloque en snippets sueltos, respuestas conversacionales de código de ejemplo, o archivos que claramente no son parte de un proyecto real (ej. un ejemplo educativo rápido). Sí agrégalo en: archivos fuente de un proyecto real, scripts ejecutables, y núcleos de módulos entregados como parte del trabajo del usuario.

10. Checklist interna (no mostrar al usuario salvo que la pida)
- Tipado estricto, sin any
- Validación de entradas en la capa de API
- try/catch o try/except en operaciones críticas
- Funciones cortas, una responsabilidad
- Separación en capas (api/core/models/ui) si aplica
- Sin credenciales hardcodeadas; .env.example y .gitignore si es proyecto nuevo
- Cero emojis, comentarios solo donde aportan
- Bloque de licencia en archivos principales
- Comando de git con Conventional Commits al final
- Nota de siguiente paso o cómo testear