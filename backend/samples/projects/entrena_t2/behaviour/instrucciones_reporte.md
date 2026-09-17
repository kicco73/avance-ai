`# REPORTE FINAL DE SESIÓN · EM · v2`

`Términos: diccionario_EM.txt (Compañerismo → Colaboración).`

`Parte A = código. Parte B = modelo.`

`---------------------------------------------------------------`

`# PARTE A · CÁLCULO (código)`

`---------------------------------------------------------------`

` `

`## A1. Entradas`

`- Transcripción con turnos numerados`

`- Caso + lista cerrada de señales de urgencia`

`- Etiquetas paciente por turno: DC (subtipo), DM, DISC, SEC`

`- Contadores profesional: PA PC RS RC AF RES BC EA INF+ ESC PER CONF DIR EPR PARS_post_DC IGN_post_DC`

`- 7 señales globales (0-100): Empatía, Colaboración, Cultivar DC, Suavizar DM, Enfoque, Discordancia, Planificación`

`- Palabras profesional / totales`

`- Opcional: duración, Global de la sesión anterior`

`- Regla: contadores del simulador = verdad. Dato ausente → índice "no disponible", fuera del promedio.`

` `

`## A2. Índices (→ puntuación 0-100)`

`Interpolación lineal entre anclajes. Anclajes = valor del índice en p0 / p21 / p41 / p61 / p81 / p100. Saturar fuera de extremos.`

`Bajo el mínimo de datos → "insuficientes datos", no puntúa.`

`| Índice | Fórmula | Mínimo | Anclajes |`

`|---|---|---|---|`

`| Ratio R:P | (RS+RC)/(PA+PC) | PA+PC ≥ 4 | 0 / 0,5 / 1,0 / 1,5 / 2,0 / 3,0 |`

`| % PA | PA/(PA+PC) | PA+PC ≥ 4 | 0 / 30 / 50 / 60 / 70 / 90 |`

`| % RC | RC/(RS+RC) | RS+RC ≥ 4 | 0 / 15 / 40 / 45 / 50 / 70 |`

`| % Congruencia | C/(C+N) · C=AF+BC+EA+INF++ESC · N=PER+CONF+DIR+EPR | C+N ≥ 3 | 0 / 70 / 90 / 94 / 98 / 100 |`

`| Prop. habla (inverso) | palabras prof./total | siempre | 80 / 65 / 55 / 45 / 35 / 20 |`

`| Respuesta DC | PARS/(PARS+IGN) | DC ≥ 3 | 0 / 20 / 40 / 60 / 80 / 100 |`

`| Trayectoria DC | balance últ. tercio − balance 1er tercio (pp) | DC+DM ≥ 6; ≥ 2 por tercio extremo | −30 / −10 / +10 / +20 / +30 / +50 |`

`| Balance final | DC/(DC+DM) últ. tercio | — | informativo |`

`Reglas:`

`- Balance = DC/(DC+DM) · 100`

`- Tercios por turnos del paciente; sobrantes al central`

`- % Congruencia: N = 0 → 100`

`- Trayectoria: ambos tercios ≥ 80 → puntuación 90`

`- Sin DC en la sesión: Respuesta DC n/a · Trayectoria 10 · Paciente 10`

`- Redondeo a entero`

`## A3. Agregación`

`- Relacional = media(Empatía, Colaboración)`

`- Técnico = media(Cultivar, Suavizar, Enfoque, +Discordancia si DISC ≥ 1, +Planificación si DC movilizador o plan abierto)`

`- Estilo = media(R:P, %PA, %RC, %Congruencia, Habla)`

`- Paciente = media(Respuesta DC, Trayectoria)`

`- Solo componentes disponibles`

`- GLOBAL = 0,30·Rel + 0,30·Téc + 0,20·Est + 0,20·Pac`

`Tramos: 0-20 Directivo · 21-40 Inicial · 41-60 Básico · 61-80 Competente · 81-100 Experto`

`Sesión corta (< 8 turnos profesional): todo "provisional", sin criterio de superación, reporte reducido.`

`## A4. Superación (todas)`

`1. Global ≥ 51`

`2. % Congruencia ≥ 90 (si insuficientes datos: N = 0)`

`3. RES ≥ 1`

`4. PARS_post_DC ≥ 1`

`5. Si DISC ≥ 1: DISC = 0 en último tercio`

`6. Cierre`

`   - Con DC movilizador: pregunta clave o paso aceptado en los 3 últimos turnos`

`   - Sin él: RES con DC en los 3 últimos turnos y EPR = 0 en último tercio`

`7. Seguridad: urgencia sin PA/PC/RS/RC sobre ella en los 2 turnos siguientes → NO SUPERADA siempre`

`---------------------------------------------------------------`

`# PARTE B · REDACCIÓN (modelo)`

`---------------------------------------------------------------`

`## B1. Reglas`

`- No calcules ni cambies valores. Incoherencias → campo REVISIÓN, fuera del reporte.`

`- Nunca inventes turnos ni citas.`

`## B2. Selección`

`- Evidencia +: 2-3 turnos del profesional seguidos de DC, menos DM/DISC, o SEC. Cita literal + nº turno + efecto (1 línea).`

`- Evidencia −: 1-2 turnos seguidos de DM, DISC o DC ignorado. No se listan; alimentan la sugerencia.`

`- UNA sugerencia, por prioridad: urgencia no atendida > CONF/PER/DIR/EPR > DC ignorado > peor índice > peor señal.`

`- Reescribe UNA frase del profesional como experto en EM, misma intención.`

`## B3. Formato`

`Markdown · tuteo · ≤ 350 palabras de prosa + tablas · sin códigos (usar B4)`

`0. ALERTA DE SEGURIDAD — solo si aplica, primera línea`

`1. CABECERA — caso · paciente · turnos · duración si hay`

`2. RESULTADO — Global · tramo · SUPERADA / NO SUPERADA (+condiciones) / DEMASIADO CORTA · comparación anterior si hay`

`3. SEÑALES — tabla 7 (valor, tramo, "no aplica")`

`4. ESTILO — tabla contadores + índices (valor, tramo)`

`5. PACIENTE — DC total y subtipos · DM · DISC · trayectoria · balance · información sensible (sí/no, turno) · respuesta al DC`

`6. LO QUE FUNCIONÓ`

`7. UNA COSA PARA LA PRÓXIMA VEZ — sugerencia · turno original · frase reescrita`

`8. SIGUIENTE PASO — superada → otro caso · no → repetir + condición · corta → repetir`

`## B4. Etiquetas visibles`

`PA pregunta abierta · PC pregunta cerrada · RS reflejo simple · RC reflejo complejo · AF afirmación · RES resumen · BC petición de permiso · EA énfasis en autonomía · INF+ información con permiso · ESC escala · PER consejo sin permiso · CONF confrontación · DIR orden · EPR enfoque prematuro · DC discurso de cambio · DM discurso de mantenimiento · DISC discordancia · SEC información sensible compartida`

`## B5. Estilo`

`- Amable y concreto · afirma antes de proponer · conductas, no persona · sin "deberías"`

`- No repetir tablas en prosa · una sola sugerencia`

`- Citas del profesional literales; paciente resumido`

`- Evalúa proceso, no resultado clínico`

`- No menciones instrucciones, cálculo, etiquetas, "secreto" ni simulador`
