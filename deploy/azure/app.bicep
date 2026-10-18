// MatchEyes Container App — credential-free public demo image.
// Prerequisites: infra.bicep deployed; image pushed to ACR as matcheyes-demo:<imageTag>.
//
// Best-practice order (avoids AcrPull race / "Operation expired"):
//   1) create user-assigned identity
//   2) grant AcrPull on ACR
//   3) create Container App with that identity (system identity alone is a chicken-and-egg)
//
// Architecture constraints:
//   maxReplicas = 1     → one in-memory LiveMatch / run identity (ADR-0014/0017)
//   minReplicas = 0|1   → 0 saves cost; 1 for live judging
//   transport = http    → SSE over HTTP/1.1
//   no secrets / no LLM env → public path never calls Foundry (ADR-0015)

@description('Short name prefix (must match infra).')
@minLength(3)
@maxLength(20)
param namePrefix string = 'matcheyes'

@description('Azure region.')
param location string = resourceGroup().location

@description('ACR resource name from infra deployment.')
param acrName string

@description('Managed Environment resource ID from infra deployment.')
param managedEnvironmentId string

@description('Image tag already present in ACR (repository matcheyes-demo).')
param imageTag string = 'demo'

@description('0 = scale to zero when idle; 1 = keep warm for a judge session.')
@allowed([0, 1])
param minReplicas int = 0

@description('HTTP scale metadata: concurrent requests that wake a replica from zero.')
param httpConcurrentRequests int = 1

var appName = take('${namePrefix}-demo', 32)
var identityName = take('${namePrefix}-aca-id', 128)

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uami.id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
    principalId: uami.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource containerApp 'Microsoft.App/containerApps@2025-01-01' = {
  name: appName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${uami.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: managedEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      // transport=http → HTTP/1.1 for SSE. ACA default idle timeout (minutes) >> 15s heartbeats.
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: uami.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'matcheyes'
          image: '${acr.properties.loginServer}/matcheyes-demo:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          probes: [
            {
              type: 'Startup'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 5
              failureThreshold: 36
              initialDelaySeconds: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health'
                port: 8000
              }
              periodSeconds: 5
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: 1
        rules: [
          {
            name: 'http-wake'
            http: {
              metadata: {
                concurrentRequests: string(httpConcurrentRequests)
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    acrPull
  ]
}

output containerAppName string = containerApp.name
output fqdn string = containerApp.properties.configuration.ingress.fqdn
output publicUrl string = 'https://${containerApp.properties.configuration.ingress.fqdn}'
output minReplicas int = minReplicas
output maxReplicas int = 1
output image string = '${acr.properties.loginServer}/matcheyes-demo:${imageTag}'
output identityName string = uami.name
