"""The element name table shared by the QTI 2 -> 3 upgrader and the QTI 3 -> 2.1 downgrader.

QTI 3 names are the QTI 2 names "kabobized" with a qti- prefix (choiceInteraction -> qti-choice-interaction),
except for a few irregular ones.
"""

from __future__ import annotations

import re
from typing import Dict, FrozenSet, Optional

QTI3_NAMESPACE = "http://www.imsglobal.org/xsd/imsqtiasi_v3p0"
QTI21_NAMESPACE = "http://www.imsglobal.org/xsd/imsqti_v2p1"

#: QTI 2.x elements (item, test and stimulus) that have a qti-* counterpart in QTI 3.
QTI2_ELEMENT_NAMES: FrozenSet[str] = frozenset(
    (
        "and anyN areaMapEntry areaMapping assessmentStimulusRef associableHotspot associateInteraction baseValue "
        "calculator calculatorInfo calculatorType card cardEntry catalog catalogInfo choiceInteraction "
        "companionMaterialsInfo containerSize contains contentBody contextDeclaration contextVariable correct "
        "correctResponse customInteraction customOperator default defaultValue delete description digitalMaterial "
        "divide drawingInteraction endAttemptInteraction equal equalRounded exitResponse "
        "exitTemplate extendedTextInteraction feedbackInline fieldValue fileHref gap gapImg gapMatchInteraction "
        "gapText gcd graphicAssociateInteraction graphicGapMatchInteraction graphicOrderInteraction gt gte "
        "hotspotChoice hotspotInteraction hottext hottextInteraction htmlContent index "
        "inlineChoice inlineChoiceInteraction inside integerDivide integerModulus integerToFloat interactionMarkup "
        "interactionModule interactionModules interpolationTable interpolationTableEntry isNull itemBody label lcm "
        "lookupOutcomeValue lt lte majorIncrement mapEntry mapResponse mapResponsePoint mapping match "
        "matchInteraction matchTable matchTableEntry mathConstant mathOperator max mediaInteraction member min "
        "minimumLength minorIncrement multiple not null numberCorrect numberIncorrect numberPresented "
        "numberResponded numberSelected or orderInteraction ordered outcomeDeclaration outcomeMaximum "
        "outcomeMinimum patternMatch physicalMaterial portableCustomInteraction positionObjectInteraction "
        "positionObjectStage power printedVariable product prompt protractor random randomFloat randomInteger "
        "repeat resourceIcon responseCondition responseDeclaration responseElse responseElseIf responseIf "
        "responseProcessing responseProcessingFragment round roundTo rule "
        "selectPointInteraction setCorrectResponse setDefaultValue setOutcomeValue setTemplateValue "
        "simpleAssociableChoice simpleChoice simpleMatchSet sliderInteraction statsOperator stringMatch stylesheet "
        "substring subtract sum templateBlock templateCondition templateConstraint templateDeclaration templateElse "
        "templateElseIf templateIf templateInline templateProcessing templateVariable textEntryInteraction truncate "
        "uploadInteraction value variable "
        # assessment test elements
        "assessmentTest testPart assessmentSection assessmentSectionRef assessmentItemRef weight outcomeProcessing "
        "outcomeCondition outcomeIf outcomeElse testVariables timeLimits itemSessionControl selection ordering "
        "adaptiveSelection adaptiveEngineRef adaptiveSettingsRef metadataRef branchRule preCondition "
        # roots and feedback containers
        "assessmentItem assessmentStimulus feedbackBlock modalFeedback rubricBlock "
        # not in the qti2xTo30.xsl lists
        "stimulusBody outcomeElseIf exitTest testFeedback templateDefault variableMapping infoControl "
        # irregular, see below
        "durationLT durationGTE incrementSI incrementUS ruleSystemSI ruleSystemUS"
    ).split()
)

#: QTI 2 names whose QTI 3 name is not the plain kabobized form (durationLT -> qti-duration-lt).
_IRREGULAR_QTI2_TO_QTI3: Dict[str, str] = {
    "durationLT": "qti-duration-lt",
    "durationGTE": "qti-duration-gte",
    "incrementSI": "qti-increment-si",
    "incrementUS": "qti-increment-us",
    "ruleSystemSI": "qti-rule-system-si",
    "ruleSystemUS": "qti-rule-system-us",
}


def kabobize(name: str) -> str:
    """baseType -> base-type (as in qti2xTo30.xsl)."""
    return re.sub(r"[A-Z]", lambda m: f"-{m.group(0).lower()}", name)


def camelize(name: str) -> str:
    """base-type -> baseType."""
    return re.sub(r"-([a-z0-9])", lambda m: m.group(1).upper(), name)


def qti_kabobify(qti2_name: str) -> str:
    """Converts any name the QTI 3 way; use for names known to be QTI elements."""
    return _IRREGULAR_QTI2_TO_QTI3.get(qti2_name) or f"qti-{kabobize(qti2_name)}"


_QTI3_TO_QTI2 = {qti_kabobify(name): name for name in QTI2_ELEMENT_NAMES}


def qti2_element_name_to_qti3(qti2_name: str) -> Optional[str]:
    """choiceInteraction -> qti-choice-interaction; None for names that are not QTI elements (e.g. XHTML)."""
    return qti_kabobify(qti2_name) if qti2_name in QTI2_ELEMENT_NAMES else None


def qti3_element_name_to_qti2(qti3_name: str) -> Optional[str]:
    """qti-choice-interaction -> choiceInteraction; None for names without the qti- prefix.

    QTI 3 elements that are not in the table (QTI 3-only) fall back to a plain camelCase of the name.
    """
    if not qti3_name.startswith("qti-"):
        return None
    return _QTI3_TO_QTI2.get(qti3_name) or camelize(qti3_name[len("qti-") :])
