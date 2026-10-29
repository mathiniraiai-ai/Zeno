"""
Kavioo 22-Language Modular Lexicon Package
"""

from .or_odia import LANGUAGE_CODE as OR_CODE, SUFFIXES as OR_SUFFIXES, LOANWORDS as OR_LOANWORDS
from .mr import LANGUAGE_CODE as MR_CODE, SUFFIXES as MR_SUFFIXES, LOANWORDS as MR_LOANWORDS
from .bn import LANGUAGE_CODE as BN_CODE, SUFFIXES as BN_SUFFIXES, LOANWORDS as BN_LOANWORDS
from .sa import LANGUAGE_CODE as SA_CODE, SUFFIXES as SA_SUFFIXES, LOANWORDS as SA_LOANWORDS
from .ja import LANGUAGE_CODE as JA_CODE, SUFFIXES as JA_SUFFIXES, LOANWORDS as JA_LOANWORDS
from .de import LANGUAGE_CODE as DE_CODE, SUFFIXES as DE_SUFFIXES, LOANWORDS as DE_LOANWORDS
from .ta import LANGUAGE_CODE as TA_CODE, SUFFIXES as TA_SUFFIXES, LOANWORDS as TA_LOANWORDS
from .ur import LANGUAGE_CODE as UR_CODE, SUFFIXES as UR_SUFFIXES, LOANWORDS as UR_LOANWORDS
from .ml import LANGUAGE_CODE as ML_CODE, SUFFIXES as ML_SUFFIXES, LOANWORDS as ML_LOANWORDS
from .pt import LANGUAGE_CODE as PT_CODE, SUFFIXES as PT_SUFFIXES, LOANWORDS as PT_LOANWORDS
from .en import LANGUAGE_CODE as EN_CODE, SUFFIXES as EN_SUFFIXES, LOANWORDS as EN_LOANWORDS
from .pa import LANGUAGE_CODE as PA_CODE, SUFFIXES as PA_SUFFIXES, LOANWORDS as PA_LOANWORDS
from .kn import LANGUAGE_CODE as KN_CODE, SUFFIXES as KN_SUFFIXES, LOANWORDS as KN_LOANWORDS
from .fr import LANGUAGE_CODE as FR_CODE, SUFFIXES as FR_SUFFIXES, LOANWORDS as FR_LOANWORDS
from .gu import LANGUAGE_CODE as GU_CODE, SUFFIXES as GU_SUFFIXES, LOANWORDS as GU_LOANWORDS
from .hi import LANGUAGE_CODE as HI_CODE, SUFFIXES as HI_SUFFIXES, LOANWORDS as HI_LOANWORDS
from .ko import LANGUAGE_CODE as KO_CODE, SUFFIXES as KO_SUFFIXES, LOANWORDS as KO_LOANWORDS
from .global_brands import LANGUAGE_CODE as GLOBAL_CODE, SUFFIXES as GLOBAL_SUFFIXES, LOANWORDS as GLOBAL_LOANWORDS
from .te import LANGUAGE_CODE as TE_CODE, SUFFIXES as TE_SUFFIXES, LOANWORDS as TE_LOANWORDS
from .as_lang import LANGUAGE_CODE as AS_CODE, SUFFIXES as AS_SUFFIXES, LOANWORDS as AS_LOANWORDS
from .id_lang import LANGUAGE_CODE as ID_CODE, SUFFIXES as ID_SUFFIXES, LOANWORDS as ID_LOANWORDS
from .es import LANGUAGE_CODE as ES_CODE, SUFFIXES as ES_SUFFIXES, LOANWORDS as ES_LOANWORDS
from .ar import LANGUAGE_CODE as AR_CODE, SUFFIXES as AR_SUFFIXES, LOANWORDS as AR_LOANWORDS

ALL_LEXICONS = {
    "or": {"code": OR_CODE, "suffixes": OR_SUFFIXES, "loanwords": OR_LOANWORDS},
    "mr": {"code": MR_CODE, "suffixes": MR_SUFFIXES, "loanwords": MR_LOANWORDS},
    "bn": {"code": BN_CODE, "suffixes": BN_SUFFIXES, "loanwords": BN_LOANWORDS},
    "sa": {"code": SA_CODE, "suffixes": SA_SUFFIXES, "loanwords": SA_LOANWORDS},
    "ja": {"code": JA_CODE, "suffixes": JA_SUFFIXES, "loanwords": JA_LOANWORDS},
    "de": {"code": DE_CODE, "suffixes": DE_SUFFIXES, "loanwords": DE_LOANWORDS},
    "ta": {"code": TA_CODE, "suffixes": TA_SUFFIXES, "loanwords": TA_LOANWORDS},
    "ur": {"code": UR_CODE, "suffixes": UR_SUFFIXES, "loanwords": UR_LOANWORDS},
    "ml": {"code": ML_CODE, "suffixes": ML_SUFFIXES, "loanwords": ML_LOANWORDS},
    "pt": {"code": PT_CODE, "suffixes": PT_SUFFIXES, "loanwords": PT_LOANWORDS},
    "en": {"code": EN_CODE, "suffixes": EN_SUFFIXES, "loanwords": EN_LOANWORDS},
    "pa": {"code": PA_CODE, "suffixes": PA_SUFFIXES, "loanwords": PA_LOANWORDS},
    "kn": {"code": KN_CODE, "suffixes": KN_SUFFIXES, "loanwords": KN_LOANWORDS},
    "fr": {"code": FR_CODE, "suffixes": FR_SUFFIXES, "loanwords": FR_LOANWORDS},
    "gu": {"code": GU_CODE, "suffixes": GU_SUFFIXES, "loanwords": GU_LOANWORDS},
    "hi": {"code": HI_CODE, "suffixes": HI_SUFFIXES, "loanwords": HI_LOANWORDS},
    "ko": {"code": KO_CODE, "suffixes": KO_SUFFIXES, "loanwords": KO_LOANWORDS},
    "global": {"code": GLOBAL_CODE, "suffixes": GLOBAL_SUFFIXES, "loanwords": GLOBAL_LOANWORDS},
    "te": {"code": TE_CODE, "suffixes": TE_SUFFIXES, "loanwords": TE_LOANWORDS},
    "as": {"code": AS_CODE, "suffixes": AS_SUFFIXES, "loanwords": AS_LOANWORDS},
    "id": {"code": ID_CODE, "suffixes": ID_SUFFIXES, "loanwords": ID_LOANWORDS},
    "es": {"code": ES_CODE, "suffixes": ES_SUFFIXES, "loanwords": ES_LOANWORDS},
    "ar": {"code": AR_CODE, "suffixes": AR_SUFFIXES, "loanwords": AR_LOANWORDS},
}
