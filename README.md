### Pre-requisites

- Install OpenShift Pipelines operator (Tekton)
- Install RHACS operator in the cluster
- Apply RBAC, Tasks, and Pipeline definitions:

```bash
oc apply -f tasks/
oc apply -f pipeline/
oc apply -f rbac/ # Also created the mycentral namespace
```

The RBAC includes the ServiceAccount `rhacs-scan-sa` in the `default` namespace and the required Role/RoleBindings.

### Run the rhacs Pipeline using tkn

> **Note:** You may need to disable the Tekton [Affinity Assistant](https://tekton.dev/docs/pipelines/affinityassistants/) to run this pipeline, since it uses two workspaces.
>
> ```sh
> oc patch configmap feature-flags -n openshift-pipelines --type=merge -p '{"data":{"coschedule":"false","disable-affinity-assistant":"true"}}'
> ```

Create the workspaces using dynamic PVCs (template provided in this repo as `pvc-template.yaml`) and start the Pipeline:

```bash
tkn pipeline start rhacs \
  -n default \
  -s rhacs-scan-sa \
  --param image=registry.redhat.io/rhel9/python-312:9.6 \
  -w name=repository,volumeClaimTemplateFile=./pvc-template.yaml \
  -w name=bin,volumeClaimTemplateFile=./pvc-template.yaml \
  --pipeline-timeout 1h \
  --showlog
```

Notes:
- Uses namespace `default` and ServiceAccount `rhacs-scan-sa`.
- Creates two 1Gi PVCs on `gp3-csi` for workspaces `repository` and `bin`.

### Alternative: Apply a PipelineRun YAML

If you prefer a declarative run, apply a `PipelineRun` manifest:

```yaml
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: rhacs-cli
  namespace: default
spec:
  pipelineRef:
    name: rhacs
  taskRunTemplate:
    serviceAccountName: rhacs-scan-sa
  timeouts:
    pipeline: 1h0m0s
  workspaces:
    - name: repository
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
          storageClassName: gp3-csi
          volumeMode: Filesystem
    - name: bin
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
          storageClassName: gp3-csi
          volumeMode: Filesystem
```

Apply and view logs:

```bash
oc apply -f run-rhacs-pr.yaml -n default
tkn pipelinerun logs -f rhacs-cli -n default
```


### Download scanner's results

Run this script locally to download the result of the scan

```bash
python download_results.py --pipeline rhacs
```

### Cleanup

To remove the RBAC objects:

```bash
oc delete -f rbac/
```
