# Mirum
**Deterministic Text-to-Assembly Orchestration for Precision Engineering**

Mirum is an AI-native text-to-CAD platform engineered to bridge the hardware-software chasm. Unlike consumer-facing AI tools that generate computationally useless polygonal meshes, Mirum outputs parametric, mathematically exact Boundary Representation (B-Rep) assemblies ready for physical manufacturing and aerodynamic simulation. 

Built with a focus on Defense and Aerospace applications, Mirum solves the "Assembly Hallucination" problem inherent in zero-shot Large Language Models by utilizing a multi-agent orchestration framework paired with a deterministic OpenCASCADE geometry kernel.

## ⚠️ The Problem: The Zero-Shot Chasm
Current foundational models fail catastrophically at engineering CAD. Natural language is too imprecise for strict mathematical tolerances, kinematic relationships, and structural constraints. If you ask an LLM for an aircraft wing, it generates a solid monolithic block of metal. It lacks the physical reasoning to generate internal spars, structural ribs, and aerodynamic skins.

## 🛠️ The Solution: Architecture
Mirum abandons zero-shot generation. Instead, it acts as a "Chief Engineer," orchestrating a pipeline of isolated subagents that write, compile, and validate Python-based `CadQuery` scripts against a live geometry engine.

### Core Components
* **Hierarchical Subagent Orchestrator (`agents.py`):** Routes tasks through a multi-step pipeline (Planner → [Machinist + Critic Loop] × N → Assembler) to prevent spatial hallucinations.
* **Domain Classifier (`classifier.py`):** An NLP gateway that routes prompts into specific geometric domains (e.g., Structural, Mechanical, Organic, Aerospace) to apply strict, domain-specific generation strategies.
* **Deterministic Utilities (`cad_utils.py`):** Bypasses the LLM's inability to do complex math by providing pre-built deterministic functions (e.g., `make_naca_wire` for exact 4-digit airfoil plotting, `make_involute_spur_gear`).
* **Isolated Execution Sandbox (`compiler.py`):** AI-generated scripts are executed in a heavily restricted, time-boxed subprocess. If the geometry kernel throws a topological error, the traceback is caught and fed back into the Critic Loop for autonomous correction.
* **Topological Mating (`schemas.py`):** Parts are generated in complete isolation and mated together by the Assembler using an `AssemblyManifest`. Absolute floating-point coordinates are strictly forbidden; all mating relies on relative topological anchors (e.g., `>Z`, `<X`).

## 🚀 Quick Start

Mirum is containerized to ensure cross-platform compatibility with the OpenCASCADE C++ libraries.

### Prerequisites
* Docker and Docker Compose
* Two Gemini API keys, split by tier:
  * `GEMINI_API_KEY_HEAVY` — used for `gemini-3.1-flash-preview` (Machinist generation, spatial-reasoning evaluation, RLAIF preference scoring, VLM continuity checks).
  * `GEMINI_API_KEY_LITE` — used for `gemini-3.1-flash-lite-preview` (domain classifier, PMI/GD&T extraction, dataset annotations, prompt stratification). They may point at the same physical key if you only have one.

### Build and Run
1. Clone the repository.
2. Set both API keys in your environment or `.env` file:
   ```bash
   export GEMINI_API_KEY_HEAVY="your_heavy_tier_key_here"
   export GEMINI_API_KEY_LITE="your_lite_tier_key_here"
   
3. Build the isolated conda environment and spin up the FastAPI server:
     docker compose up --build

   The streaming SSE endpoint will be available at http://localhost:8000/generate.


✈️ Aerospace Execution Flow
When a user prompts: "Generate a tapered aircraft wing segment using a NACA 2412 airfoil, length 500mm, with three internal structural ribs"

Classify: classifier.py flags the prompt as Domain D (Aerospace).

Plan: The Planner agent drafts an AssemblyManifest defining four distinct parts: wing_skin, rib_1, rib_2, and rib_3.

Draft & Compile: * The Machinist agent drafts the code, explicitly using the make_naca_wire() utility to generate perfect aerodynamic curves.

It uses Boolean intersections to automatically cut the internal ribs so they mate perfectly flush against the inside of the hollowed wing skin.

Assemble: The Assembler reads the manifest and topologically mates the internal ribs to the specified flight stations.

Export: The backend streams a manifold, collision-free .glb assembly file to the client.

💻 Tech Stack
LLM Engine: Google Gemini 3.1 Flash Preview (heavy tier) + Gemini 3.1 Flash Lite Preview (lite tier), via google-generativeai

Geometry Kernel: CadQuery / OpenCASCADE

Backend: FastAPI, Pydantic, Uvicorn

Environment: Miniconda3 (conda-forge)

  
