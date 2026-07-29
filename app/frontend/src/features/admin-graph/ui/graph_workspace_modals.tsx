import type { TFunction } from "i18next";

import styles from "./AdminGraphWorkspace.module.css";
import { Button, Card, Input, Modal, Textarea } from "../../../shared/ui/primitives";

interface NodeEditorModalProps {
  open: boolean;
  mode: "create" | "edit";
  label: string;
  propertiesText: string;
  t: TFunction;
  onClose: () => void;
  onLabelChange: (value: string) => void;
  onPropertiesChange: (value: string) => void;
  onSubmit: () => void;
}

export const NodeEditorModal = ({
  open,
  mode,
  label,
  propertiesText,
  t,
  onClose,
  onLabelChange,
  onPropertiesChange,
  onSubmit,
}: NodeEditorModalProps) => (
  <Modal open={open} onClose={onClose}>
    <div className={styles.modalStack}>
      <h2>
        {t(mode === "create" ? "admin.graphActions.createNode" : "admin.graphActions.editNode")}
      </h2>
      {mode === "create" ? (
        <Input
          value={label}
          onChange={(event) => onLabelChange(event.target.value)}
          placeholder={t("admin.graphWorkspace.nodeLabel")}
        />
      ) : null}
      <Textarea
        value={propertiesText}
        onChange={(event) => onPropertiesChange(event.target.value)}
      />
      <div className="muted">{t("admin.graphWorkspace.jsonHint")}</div>
      <div className={styles.inlineActions}>
        <Button tone="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
        <Button onClick={onSubmit}>{t("common.save")}</Button>
      </div>
    </div>
  </Modal>
);

interface RelationshipEditorModalProps {
  open: boolean;
  sourceNodeId: string;
  targetNodeId: string;
  relationshipType: string;
  propertiesText: string;
  relationshipTypes: string[];
  t: TFunction;
  onClose: () => void;
  onSourceNodeIdChange: (value: string) => void;
  onTargetNodeIdChange: (value: string) => void;
  onRelationshipTypeChange: (value: string) => void;
  onPropertiesChange: (value: string) => void;
  onSubmit: () => void;
}

export const RelationshipEditorModal = ({
  open,
  sourceNodeId,
  targetNodeId,
  relationshipType,
  propertiesText,
  relationshipTypes,
  t,
  onClose,
  onSourceNodeIdChange,
  onTargetNodeIdChange,
  onRelationshipTypeChange,
  onPropertiesChange,
  onSubmit,
}: RelationshipEditorModalProps) => (
  <Modal open={open} onClose={onClose}>
    <div className={styles.modalStack}>
      <h2>{t("admin.graphActions.createRelationship")}</h2>
      <Input
        value={sourceNodeId}
        onChange={(event) => onSourceNodeIdChange(event.target.value)}
        placeholder={t("admin.graphWorkspace.sourceNodeId")}
      />
      <Input
        value={targetNodeId}
        onChange={(event) => onTargetNodeIdChange(event.target.value)}
        placeholder={t("admin.graphWorkspace.targetNodeId")}
      />
      <Input
        value={relationshipType}
        onChange={(event) => onRelationshipTypeChange(event.target.value)}
        placeholder={t("admin.graphWorkspace.relationshipType")}
        list="relationship-types"
      />
      <datalist id="relationship-types">
        {relationshipTypes.map((item) => (
          <option key={item} value={item} />
        ))}
      </datalist>
      <Textarea
        value={propertiesText}
        onChange={(event) => onPropertiesChange(event.target.value)}
      />
      <div className={styles.inlineActions}>
        <Button tone="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
        <Button onClick={onSubmit}>{t("common.save")}</Button>
      </div>
    </div>
  </Modal>
);

interface CypherQueryModalProps {
  open: boolean;
  query: string;
  rows: Record<string, unknown>[];
  t: TFunction;
  onClose: () => void;
  onQueryChange: (value: string) => void;
  onSubmit: () => void;
}

export const CypherQueryModal = ({
  open,
  query,
  rows,
  t,
  onClose,
  onQueryChange,
  onSubmit,
}: CypherQueryModalProps) => (
  <Modal open={open} onClose={onClose}>
    <div className={styles.modalStack}>
      <h2>{t("admin.graphActions.runCypher")}</h2>
      <Textarea value={query} onChange={(event) => onQueryChange(event.target.value)} />
      <div className={styles.inlineActions}>
        <Button tone="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
        <Button onClick={onSubmit}>{t("common.submit")}</Button>
      </div>
      <Card title={t("admin.graphWorkspace.cypherResult")}>
        <div className={styles.mono}>{JSON.stringify(rows, null, 2)}</div>
      </Card>
    </div>
  </Modal>
);
