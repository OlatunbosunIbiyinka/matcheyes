// Shared infra for the MatchEyes demo (ACR + Log Analytics + Container Apps Environment).
// Deploy this before building/pushing the image, then deploy app.bicep.

@description('Short name prefix for Azure resources (letters/numbers only).')
@minLength(3)
@maxLength(20)
param namePrefix string = 'matcheyes'

@description('Azure region for all resources.')
param location string = resourceGroup().location

var suffix = uniqueString(resourceGroup().id, namePrefix)
var acrName = toLower('me${take(replace(namePrefix, '-', ''), 8)}${take(suffix, 6)}')
var envName = take('${namePrefix}-env', 60)
var lawName = take('${namePrefix}-logs', 63)

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: lawName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource managedEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output acrId string = acr.id
output managedEnvironmentId string = managedEnv.id
output managedEnvironmentName string = managedEnv.name
output logAnalyticsName string = logAnalytics.name
