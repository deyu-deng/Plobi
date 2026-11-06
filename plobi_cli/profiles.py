"""退役中的门牌：实现已整体搬到 ``plobi_cli/agent_identity.py``。

这里**不留任何实现**，只做一次属性转发。留着它的唯一理由：``plobi/console/router.py``
在 ``WP-L3-EXPERTS`` 名下在派，本线不签别人在飞的档；她那笔落地把那一跳改成
``plobi_cli.agent_identity`` 之后，这一档直接删掉。

判据：``tests/plobi_cli/test_plobi_profiles_is_a_thin_stub.py``——这档里出现
``def`` / ``class`` / 除转发以外的赋值即红，所以它只会越来越薄，不会长回一套实现。
"""

from __future__ import annotations

import plobi_cli.agent_identity as _identity


def __getattr__(name):  # PEP 562：`from plobi_cli.profiles import X` 走这里
    return getattr(_identity, name)


def __dir__():
    return dir(_identity)
