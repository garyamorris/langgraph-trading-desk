# trading_desk walkthrough: transcript

Narration as spoken (OpenAI `gpt-4o-mini-tts`, voice marin). Captions use this text, timed to the real audio by a `whisper-1` word-level alignment.

## [0:03.9] A

This is a hedge fund trading desk, built as a single LangGraph: nineteen nodes, with six analysts, a bull and a bear, four strategy desks, a risk committee, and an execution router. Everything you are about to see is a real run, recorded against the live graph with real models.

## [0:22.5] B

Nodes never call each other. They share one typed state, thirteen channels. Each node reads what it needs and returns only the keys it changed. When parallel branches write to the same channel, a reducer decides how to combine them: five channels here merge dictionaries, so six analysts can write at once without overwriting each other.

## [0:44.1] C

The run starts at the CIO router, which fans out in two ways. Six analysts start at once. And a Send call spawns one deep research worker per ticker: the same function, mapped over six inputs. They all run in one super-step, so the aggregator fires only when the slowest branch lands, and the reducers have merged twelve parallel writes into the state.

## [1:08.7] D

The aggregator feeds two nodes that run in parallel: a bull advocate and a bear critic, reading the same state and arguing opposing cases. Then the arbiter rules. In this run it settled on market neutral dispersion, and split the capital: 15% momentum, 35% stat arb, 30% macro vol, 20% event arb.

## [1:32.8] E

Next, the desks. The arbiter returns one Send per desk, but there is only one desk subgraph, compiled once with its own input and output schemas, and run four times in parallel. A quant models the legs, a desk head signs off, and the quant's draft stays in a private channel. Only the final proposal flows back to shared state.

## [1:56.0] F

Now the gate. Two auditors run in parallel, and the chief risk officer reads them. But the language model does not get the final say: a deterministic risk engine computes exposure and value at risk, and its verdict is authoritative. Here it flags the momentum desk: a TLT position of $14M, against a $12M cap.

## [2:17.4] G

The conditional edge returns the verdict revise, and the router sends only the offending desk around again, with a fresh Send. The revision counter moves from zero to one. Momentum comes back with a book of $11.5M gross, the engine re-checks, and this time it approves: about 98.5% gross, 9.5% net, value at risk under the limit.

## [2:43.7] H

The loop is bounded to two rounds. If breaches are still open after that, the graph does not approve by default: a rejected verdict routes to a halt node, and the run ends with no trade. That branch did not fire in this recording; the test suite covers it.

## [3:01.0] I

Approved, so the head of trading releases the blotter: $25.2M gross, scaled and hedged. The execution router slices seven parent orders into child orders across dark pools, IEX and lit venues, with about $43k of estimated cost, and the run reaches its end.

## [3:22.0] K

One shared state, parallel fan-outs, a subgraph reused four times, a deterministic gate with a bounded loop, and a fail-closed exit. Run it with langgraph dev and watch it in Studio. It is a demo on paper data, and it is not trading advice.
