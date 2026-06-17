# Shiftgrid

This is a platform for agentic pentesting with human oversight. A workspAIce, if you want to call it that. Workflows define the agentic flow. Created and controlled by humans. Checklists, state machines, and project notes help keep track of all testing and provide transparency and traceability. It enables testing that go beyond the "just throw more agents at it" mentality, making it highly token-efficient.

Ideas behind this project:
- Like humans, agents need structure to get stuff done
- Observations preserve knowledge without bloating agent context windows
- Agents can work in parallel while seeing the others progress
- API for Agents : Web UI for humans
- Displays work process and thoughts of the agents live
- Pentesting is a searching problem. This benefits from a structured approach
- Models are smart
- Workflows can point to specialized agents (reseacher, script developer, domain experts..)
- Checklists prevent missed tests
- Endpoint list prevent missed scope

All in all it comes down to:
- LLM token stream (thinking) -> ChatGPT (talking) -> Claude Code agent (acting) -> Workflows (working)
- context window (short-term mem) -> notes page (project-wide mem) -> observations (detailed, long-term mem)

The tool is independent of the workflows, checklists, agents, and models you use. As a result, a simple workflow using a low-cost model can produce dramatically different output from a more detailed workflow using premium models running concurrently. The default workflow and checklist are intentionally kept minimal.


# GIF
TODO


# Installation

1. Download this repository
2. Docker compose up --build
3. Create a project at localhost:8000
4. Point your agent(s) (claude, pi, ..) to it: "Do the work on the ShiftGrid at http://localhost:8001/api/v1/help"


# Security model
This is a local application started via Docker and bound to 127.0.0.1. One port is used for the API (agent access) and one port for the web UI (human operator access). There is no login or authentication mechanism. Separation of agent and human operator privileges is enforced through the port. Human operator authentication is provided by the operating system. Do not expose these ports to the wider network.

Prompt Injection: When testing applications that contain prompt injection payloads, the first line of defense is a secure agent running in an isolated environment. If the agent is compromised, assume it has access to all data collected during the pentest. In that case, the pentest environment and its data must be considered compromised as well.


# Problem of Malicious Humans
At it's core this is primarily an API for managing testing workflows through checklist and task tracking. It does not provide exploits or malicious tools or scripts. However, it lowers the barrier for attacking - especially with the right workflows. At the same time it lowers the barrier and costs for real beneficial pentests and therefore gives the opportunity to secure many systems.

A certain degree of friction was intentionally left in the project. Plus, the default workflow was chosen to not get into too much detail. This is a rather shallow testing. But none of this stops a determined attacker and we should not be pretending it does. A motivated bad actor strips the friction in minutes, improves workflows and modifies the ShiftGrid source code itself with an AI coding agent - that's a fact.

worst-case: script-kiddy with a high-end model attacks critical infrastructure for some reason.
best-case: IT guys in companies use this tool to secure their environment on a regular basis.


# Problem of Malicious AIs
Well, shit. It gets even worse.

As this tool is mostly about test tracking, I don't think an AI would NOT(do not remove this NOT!) be able to create this itself. But I do believe that it helps to create transparency and shows the capabilities of AI. So we can adapt and act accordingly.

best-case: Autonomous AIs use it to make hacking almost impossible by finding all bugs everywhere.
worst-case: This tool inspires a predator-minded AI on its self-improving journey.
