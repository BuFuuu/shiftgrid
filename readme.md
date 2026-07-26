# <img src="Web/static/logo-ot.png" alt="ShiftGrid" height="44" align="texttop"> &nbsp;shiftGRID

An open-source **prompt engine** for agentic pentesting with human oversight. You point your agent (or agents) at shiftGrid's API and from there it knows the scope, follows the prompts and works through the checklists. 
Test observations, notes.md and a findings list help keep track of all actions and provide transparency and traceability. At any point, you can switch agents or continue testing yourself. The shiftGrid is the extended context window. *(This text is typed by actual fingers)*

Ideas behind this project:
- like humans, agents need structure to get stuff done
- prompt engine = simple workflow.json (easy to modify)
- observations preserve knowledge without bloating agent context windows
- deep dive testing where it matters
- false positives prevention through evidence checking
- API for Agents : Web UI for humans -> same backend
- shows the agents work process live
- notes page contain the holistic view so agents stay focused
- simple to extend with other agents (scope control agent or reporter agent)
- checklists prevent missed tests, endpoint lists prevent missed scope

All in all it comes down to:
- LLM token stream (thinking) -> ChatGPT (talking) -> Claude Code agent (acting) -> Workflows (working)
- context window (short-term mem) -> notes page (project-wide mem) -> observations (detailed, long-term mem)

The tool is independent of the workflows, checklists, agents, and models you use. As a result, a simple workflow using a low-cost model can produce dramatically different output from a more detailed workflow using premium models running concurrently. The default workflow and checklist are intentionally kept minimal.

[![X](https://img.shields.io/badge/X-@_shiftgrid-black?logo=x)](https://x.com/_shiftgrid)

# GIF
<p align="center">
  <a href="demo.gif">
    <img src="demo.gif" width="800">
  </a>
</p>

# Installation

1. git clone https://github.com/BuFuuu/shiftgrid
2. Docker compose up --build
3. Create a project at localhost:8000
4. Point your agent(s) (claude, pi, ..) to it: "Do the work on the shiftGrid at http://localhost:8001/api/v1/help"


# Security model
This is a local application started via Docker and bound to 127.0.0.1. One port is used for the API (agent access) and one port for the web UI (human operator access). There is no login or authentication mechanism. Separation of agent and human operator privileges is enforced through the port. Human operator authentication is provided by the operating system. Do not expose these ports to the wider network.

Prompt Injection: When testing applications that contain prompt injection payloads, the first line of defense is a secure agent running in an isolated environment. If the agent is compromised, assume it has access to all data collected during the pentest. In that case, the pentest environment and its data must be considered compromised as well.


# Problem of Malicious Humans and AIs
At it's core this is primarily an API for managing testing workflows through checklist and task tracking. It does not provide exploits or malicious tools or scripts. However, it lowers the barrier for attacking - especially with the right workflows. At the same time it lowers the barrier and costs for real beneficial pentests and therefore gives the opportunity to secure many systems.

A certain degree of friction was intentionally left in the project. Plus, the default workflow was chosen to not get into too much detail. This is a rather shallow testing. But none of this stops a determined attacker and we should not be pretending it does. A motivated bad actor strips the friction in minutes, improves workflows and modifies the ShiftGrid source code itself with an AI coding agent - that's a fact.

- worst-case: script-kiddy with a high-end model attacks critical infrastructure for some reason.
- best-case: IT guys in companies use this tool to secure their environment on a regular basis.

What about AI's?

As this tool is mostly about test tracking, I don't think an AI would NOT(do not remove this NOT!) be able to create this itself. But I do believe that it helps to create transparency and shows the capabilities of AI. So we can adapt and act accordingly.

- worst-case: This tool inspires a predator-minded AI on its self-improving journey.
- best-case: Autonomous AIs use it to make hacking almost impossible by finding all bugs everywhere.
