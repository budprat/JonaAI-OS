import { useState, useCallback } from 'react'
import { FolderOpen, Plus, Trash2, Check, X, PenLine, FileText, RefreshCw, ChevronDown, ChevronRight, FolderPlus, Image, FileSpreadsheet, FileCode, Map, Download } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { Project, ProjectFile, JobFile } from '@/types'
import { cn, formatDateTime, formatBytes, downloadAuthenticatedFile } from '@/lib/utils'
import { useAuth } from '@/components/auth/useAuth'

// Focuses an inline editor once, when the user opens it. Stable, so React calls it only on
// mount rather than on every render; the autoFocus prop does the same but fails jsx-a11y.
const focusOnMount = (el: HTMLInputElement | null) => el?.focus()

const FILE_ICONS: Record<string, typeof FileText> = {
  tif: Map,
  tiff: Map,
  png: Image,
  jpg: Image,
  jpeg: Image,
  csv: FileSpreadsheet,
  html: FileCode,
  md: FileCode,
  json: FileCode,
}

function getFileIcon(ext: string) {
  return FILE_ICONS[ext] || FileText
}

function getFileLabel(ext: string): string {
  const labels: Record<string, string> = {
    tif: 'GeoTIFF',
    tiff: 'GeoTIFF',
    png: 'Image',
    jpg: 'Image',
    csv: 'CSV Data',
    html: 'Report',
    md: 'Document',
    json: 'JSON',
  }
  return labels[ext] || ext.toUpperCase()
}

interface ProjectsPanelProps {
  projects: Project[]
  unassignedFiles?: ProjectFile[]
  activeProjectId: string | null
  onSelectProject: (project: Project) => void
  onCreateProject: (name: string, description: string) => void
  onDeleteProject: (id: string) => void
  onRenameProject: (id: string, name: string) => void
  onAssignToProject?: (saveId: string, projectId: string | null) => void
  onRefresh?: () => void
  className?: string
}

export function ProjectsPanel({
  projects,
  unassignedFiles = [],
  activeProjectId,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onRenameProject,
  onAssignToProject,
  onRefresh,
  className,
}: ProjectsPanelProps) {
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set(['__unassigned__']))
  const [assigningFile, setAssigningFile] = useState<string | null>(null)

  const handleCreate = useCallback(() => {
    const name = newName.trim()
    if (!name) return
    onCreateProject(name, newDesc.trim())
    setNewName('')
    setNewDesc('')
    setCreating(false)
  }, [newName, newDesc, onCreateProject])

  const handleRename = useCallback((id: string) => {
    const name = editName.trim()
    if (!name) return
    onRenameProject(id, name)
    setEditingId(null)
  }, [editName, onRenameProject])

  const toggleExpand = useCallback((id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const totalFiles = unassignedFiles.length + projects.reduce((sum, p) => sum + (p.files?.length || p.analysis_count || 0), 0)

  return (
    <Card className={cn("flex flex-col border-0 shadow-none", className)}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-1">
          <CardTitle className="flex items-center gap-2 text-sm font-semibold">
            <FolderOpen className="h-4 w-4 text-muted-foreground" />
            Projects
            {totalFiles > 0 && (
              <Badge variant="secondary" className="text-[10px]">{totalFiles} results</Badge>
            )}
          </CardTitle>
          <div className="flex gap-0.5">
            {onRefresh && (
              <Button variant="ghost" size="icon" onClick={onRefresh} title="Refresh" data-testid="button-refresh-projects">
                <RefreshCw className="h-3.5 w-3.5" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setCreating(true)}
              title="New Project"
              data-testid="button-new-project"
            >
              <Plus className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex-1 overflow-auto p-4 pt-0">
        {creating && (
          <div className="mb-3 rounded-lg border bg-muted/30 p-3 space-y-2">
            <input
              ref={focusOnMount}
              type="text"
              placeholder="Project name"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              className="w-full rounded-md border bg-background px-2.5 py-1.5 text-sm outline-none focus:ring-1 focus:ring-ring"
              data-testid="input-project-name"
            />
            <input
              type="text"
              placeholder="Description (optional)"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              className="w-full rounded-md border bg-background px-2.5 py-1.5 text-xs outline-none focus:ring-1 focus:ring-ring"
              data-testid="input-project-desc"
            />
            <div className="flex justify-end gap-1">
              <Button variant="ghost" size="sm" onClick={() => setCreating(false)} className="h-7 px-2 text-xs">
                Cancel
              </Button>
              <Button size="sm" onClick={handleCreate} disabled={!newName.trim()} className="h-7 px-2 text-xs" data-testid="button-create-project">
                Create
              </Button>
            </div>
          </div>
        )}

        {totalFiles === 0 && projects.length === 0 && !creating ? (
          <div className="flex flex-col items-center py-8 text-center">
            <div className="mb-3 rounded-xl bg-muted/50 p-3">
              <FolderOpen className="h-6 w-6 text-muted-foreground/50" />
            </div>
            <p className="text-sm text-muted-foreground">No projects or saved files yet</p>
            <p className="mt-1 text-xs text-muted-foreground/70">Run an analysis and save results to see them here</p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3 text-xs"
              onClick={() => setCreating(true)}
              data-testid="button-create-first-project"
            >
              <Plus className="mr-1 h-3 w-3" />
              Create Project
            </Button>
          </div>
        ) : (
          <div className="space-y-2">
            {projects.map((project) => {
              const isExpanded = expandedIds.has(project.id)
              const files = project.files || []
              const fileCount = project.analysis_count ?? files.length

              return (
                <div
                  key={project.id}
                  className={cn(
                    "rounded-lg border transition-colors",
                    activeProjectId === project.id && "border-primary/50 bg-primary/5"
                  )}
                  data-testid={`card-project-${project.id}`}
                >
                  <div
                    role="button"
                    tabIndex={0}
                    className="cursor-pointer p-3 hover:bg-muted/30"
                    onClick={() => onSelectProject(project)}
                    onKeyDown={(e) => {
                      if (e.target !== e.currentTarget) return
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        onSelectProject(project)
                      }
                    }}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        {editingId === project.id ? (
                          <div className="flex items-center gap-1">
                            <input
                              ref={focusOnMount}
                              type="text"
                              value={editName}
                              onChange={(e) => setEditName(e.target.value)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') handleRename(project.id)
                                if (e.key === 'Escape') setEditingId(null)
                              }}
                              onClick={(e) => e.stopPropagation()}
                              className="w-full rounded border bg-background px-1.5 py-0.5 text-sm outline-none focus:ring-1 focus:ring-ring"
                            />
                            <Button variant="ghost" size="icon" onClick={(e) => { e.stopPropagation(); handleRename(project.id) }}>
                              <Check className="h-3 w-3" />
                            </Button>
                            <Button variant="ghost" size="icon" onClick={(e) => { e.stopPropagation(); setEditingId(null) }}>
                              <X className="h-3 w-3" />
                            </Button>
                          </div>
                        ) : (
                          <h4 className="truncate text-sm font-medium">{project.name}</h4>
                        )}
                        {project.description && editingId !== project.id && (
                          <p className="mt-0.5 truncate text-xs text-muted-foreground">{project.description}</p>
                        )}
                      </div>
                      {activeProjectId === project.id && editingId !== project.id && (
                        <Badge variant="default" className="shrink-0 text-[10px]">Active</Badge>
                      )}
                    </div>

                    <div className="mt-2 flex items-center justify-between gap-1">
                      <button
                        type="button"
                        className="flex items-center gap-1 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
                        onClick={(e) => {
                          e.stopPropagation()
                          toggleExpand(project.id)
                        }}
                        data-testid={`button-expand-project-${project.id}`}
                      >
                        {isExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
                        {fileCount} {fileCount === 1 ? 'result' : 'results'}
                      </button>
                      {confirmDelete === project.id ? (
                        <div className="flex items-center gap-1">
                          <span className="text-[10px] text-destructive">Delete project & all files?</span>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={(e) => { e.stopPropagation(); setConfirmDelete(null) }}
                            data-testid={`button-cancel-delete-${project.id}`}
                          >
                            <X className="h-3 w-3" />
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={(e) => {
                              e.stopPropagation()
                              onDeleteProject(project.id)
                              setConfirmDelete(null)
                            }}
                            data-testid={`button-confirm-delete-${project.id}`}
                          >
                            <Check className="h-3 w-3" />
                          </Button>
                        </div>
                      ) : (
                        <div className="flex gap-0.5">
                          <Button
                            variant="ghost"
                            size="icon"
                            title="Rename project"
                            onClick={(e) => {
                              e.stopPropagation()
                              setEditingId(project.id)
                              setEditName(project.name)
                            }}
                            data-testid={`button-rename-project-${project.id}`}
                          >
                            <PenLine className="h-3 w-3" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="text-destructive"
                            title="Delete project"
                            onClick={(e) => { e.stopPropagation(); setConfirmDelete(project.id) }}
                            data-testid={`button-delete-project-${project.id}`}
                          >
                            <Trash2 className="h-3 w-3" />
                          </Button>
                        </div>
                      )}
                    </div>
                  </div>

                  {isExpanded && (
                    <div className="border-t px-3 pb-3">
                      {files.length === 0 ? (
                        <p className="py-3 text-center text-xs text-muted-foreground">
                          No saved files yet. Save an analysis result to add it here.
                        </p>
                      ) : (
                        <div className="mt-2 space-y-2">
                          {files.map((file) => (
                            <SavedResultItem key={file.save_id} file={file} expandedIds={expandedIds} toggleExpand={toggleExpand} />
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}

            {unassignedFiles.length > 0 && (
              <div className="rounded-lg border" data-testid="section-unassigned-files">
                <button
                  type="button"
                  className="flex w-full items-center justify-between p-3 text-left hover:bg-muted/30 transition-colors"
                  onClick={() => toggleExpand('__unassigned__')}
                  data-testid="button-expand-unassigned"
                >
                  <div className="flex items-center gap-2">
                    <FileText className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm font-medium">Saved Results</span>
                    <Badge variant="secondary" className="text-[10px]">{unassignedFiles.length}</Badge>
                  </div>
                  {expandedIds.has('__unassigned__') ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
                </button>
                {expandedIds.has('__unassigned__') && (
                  <div className="border-t px-3 pb-3">
                    <div className="mt-2 space-y-2">
                      {unassignedFiles.map((file) => (
                        <SavedResultItem
                          key={file.save_id}
                          file={file}
                          expandedIds={expandedIds}
                          toggleExpand={toggleExpand}
                          projects={projects}
                          assigningFile={assigningFile}
                          onStartAssign={setAssigningFile}
                          onAssign={onAssignToProject}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function SavedResultItem({ file, expandedIds, toggleExpand, projects, assigningFile, onStartAssign, onAssign }: {
  file: ProjectFile
  expandedIds: Set<string>
  toggleExpand: (id: string) => void
  projects?: Project[]
  assigningFile?: string | null
  onStartAssign?: (id: string | null) => void
  onAssign?: (saveId: string, projectId: string | null) => void
}) {
  const jobFiles = file.job_files || []
  const isExpanded = expandedIds.has(`files_${file.save_id}`)
  const isAssigning = assigningFile === file.save_id
  const totalJobSize = jobFiles.reduce((s, f) => s + f.size_bytes, 0)

  return (
    <div className="rounded-md border bg-muted/20" data-testid={`result-${file.save_id}`}>
      <div className="flex items-center gap-2 px-2.5 py-2">
        <button
          type="button"
          className="shrink-0 text-muted-foreground hover:text-foreground transition-colors"
          onClick={() => toggleExpand(`files_${file.save_id}`)}
          data-testid={`button-expand-files-${file.save_id}`}
        >
          {isExpanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </button>
        <div className="flex-1 min-w-0">
          <p className="truncate text-xs font-medium">{file.title}</p>
          <p className="text-[10px] text-muted-foreground">
            {jobFiles.length} {jobFiles.length === 1 ? 'file' : 'files'} &middot; {formatBytes(totalJobSize || file.size_bytes)} &middot; {formatDateTime(file.created_at)}
          </p>
        </div>
        {projects && projects.length > 0 && onStartAssign && (
          <Button
            variant="ghost"
            size="icon"
            title="Move to project"
            onClick={() => onStartAssign(isAssigning ? null : file.save_id)}
          >
            <FolderPlus className="h-3 w-3" />
          </Button>
        )}
      </div>

      {isAssigning && projects && onAssign && (
        <div className="border-t px-3 py-2 space-y-1">
          <p className="text-[10px] text-muted-foreground mb-1">Move to project:</p>
          {projects.map((p) => (
            <button
              key={p.id}
              type="button"
              className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-[11px] hover:bg-muted/50 transition-colors text-left"
              onClick={() => {
                onAssign(file.save_id, p.id)
                onStartAssign?.(null)
              }}
              data-testid={`assign-${file.save_id}-to-${p.id}`}
            >
              <FolderOpen className="h-3 w-3 text-muted-foreground shrink-0" />
              <span className="truncate">{p.name}</span>
            </button>
          ))}
        </div>
      )}

      {isExpanded && jobFiles.length > 0 && (
        <div className="border-t px-2.5 pb-2 pt-1.5 space-y-1">
          {jobFiles.map((jf) => (
            <JobFileRow key={jf.name} file={jf} saveId={file.save_id} />
          ))}
        </div>
      )}

      {isExpanded && jobFiles.length === 0 && (
        <div className="border-t px-3 py-2">
          <p className="text-[10px] text-muted-foreground text-center">No individual files found</p>
        </div>
      )}
    </div>
  )
}

export function JobFileRow({ file, saveId }: { file: JobFile; saveId: string }) {
  const Icon = getFileIcon(file.ext)
  const { accessToken } = useAuth()
  const downloadUrl = `/saved-jobs/${saveId}/files/${encodeURIComponent(file.name)}`

  return (
    <div className="flex items-center gap-2 rounded px-2 py-1.5 text-[11px] hover:bg-muted/30 transition-colors group" data-testid={`jobfile-${saveId}-${file.name}`}>
      <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <div className="flex-1 min-w-0">
        <p className="truncate font-medium">{file.name}</p>
        <p className="text-[10px] text-muted-foreground">
          {getFileLabel(file.ext)} &middot; {formatBytes(file.size_bytes)}
        </p>
      </div>
      <a
        href={downloadUrl}
        download={file.name}
        target="_blank"
        rel="noopener noreferrer"
        className="shrink-0 invisible group-hover:visible text-muted-foreground hover:text-foreground transition-colors"
        title={`Download ${file.name}`}
        onClick={(e) => {
          // The route needs the Authorization header, which a plain link can't send.
          e.preventDefault()
          e.stopPropagation()
          downloadAuthenticatedFile(downloadUrl, file.name, accessToken).catch((err) =>
            console.error('[ProjectsPanel] download failed', err),
          )
        }}
        data-testid={`download-${saveId}-${file.name}`}
      >
        <Download className="h-3 w-3" />
      </a>
    </div>
  )
}
