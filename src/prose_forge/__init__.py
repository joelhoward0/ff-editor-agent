"""prose-forge: draft fiction in your voice, then strip the AI-isms.

A local, file-based agentic pipeline. Every stage writes plain files to a run
directory; style is enforced post-hoc by deterministic lint + surgical LLM
edits, never by "avoid X" instructions in the drafter.
"""

__version__ = "0.1.0"
