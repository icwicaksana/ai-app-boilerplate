"""Skills on-demand (v0.9).

Modul di paket ini SENGAJA murni: tanpa I/O, tanpa jaringan, tanpa DB.
Pemuatan dari disk ada di `loader.py` (F2); pemakaian oleh agen di
`app/agents/tools/SkillTools.py` (F3).

Kontrak frontmatter (dikunci sejak skill pertama ditulis — menambah field
belakangan berarti migrasi konten di SEMUA fork klien):

    ---
    name: sop-garansi
    description: Prosedur klaim garansi 12 bulan, syarat, SLA 3 hari
    when_to_use: user menanyakan garansi, klaim rusak, atau masa berlaku
    tags: [garansi, klaim]
    ---
    # Prosedur Garansi
    ...
"""

from app.skills.model import Skill, SkillError, SkillIndexEntry

__all__ = ["Skill", "SkillError", "SkillIndexEntry"]
