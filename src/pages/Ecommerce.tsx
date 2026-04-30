import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Clipboard, Download, ImagePlus, Loader2, Maximize2, Paperclip, PencilLine, RefreshCw, Sparkles, Trash2, X } from 'lucide-react';
import {
  deleteHistory,
  editHistoryImage,
  formatDate,
  generateEcommercePublishCopy,
  generateEcommerceImages,
  getConfig,
  getHistory,
  type EcommercePublishCopyResult,
  HistoryItem,
} from '../api';
import CompactInput from '../components/CompactInput';
import GenerationSelect from '../components/GenerationSelect';
import ImagePreviewModal from '../components/ImagePreviewModal';
import PromptEditorModal from '../components/PromptEditorModal';
import { copyTextToClipboard } from '../clipboard';
import { groupHistoryItems, HistoryGroup, mergeHistoryItems } from '../historyGroups';
import {
  ASPECT_RATIO_OPTIONS,
  IMAGE_COUNT_OPTIONS,
  isSupportedImagePreset,
  normalizeImageScale,
  providerImageSize,
  QUALITY_OPTIONS,
  SIZE_LABELS,
  SIZE_OPTIONS,
} from '../imageOptions';
import { useSite } from '../site';
import { useTasks } from '../tasks';

const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];

export default function Ecommerce() {
  const { t } = useSite();
  const { addTask, openDrawer, taskHistoryItems } = useTasks();
  const [productImage, setProductImage] = useState<File | null>(null);
  const [productPreview, setProductPreview] = useState<{ name: string; url: string } | null>(null);
  const [form, setForm] = useState({
    productName: '',
    materials: '',
    sellingPoints: '',
    scenarios: '',
    platform: '淘宝/抖音',
    style: '高级、干净、统一电商详情页',
    extraRequirements: '',
  });
  const [imageScale, setImageScale] = useState('2K');
  const [aspectRatio, setAspectRatio] = useState('1:1');
  const [imageQuality, setImageQuality] = useState('auto');
  const [imageCount, setImageCount] = useState('4');
  const [historyItems, setHistoryItems] = useState<HistoryItem[]>([]);
  const [selectedGroupKey, setSelectedGroupKey] = useState<string | null>(null);
  const [previewItem, setPreviewItem] = useState<{ imageUrl: string | null; prompt: string; referenceUrl?: string | null } | null>(null);
  const [editingItem, setEditingItem] = useState<HistoryItem | null>(null);
  const [editPrompt, setEditPrompt] = useState('');
  const [editReferenceFiles, setEditReferenceFiles] = useState<File[]>([]);
  const [editReferencePreviews, setEditReferencePreviews] = useState<{ name: string; url: string }[]>([]);
  const [publishCopies, setPublishCopies] = useState<Record<string, EcommercePublishCopyResult>>({});
  const [publishCopyLoadingKey, setPublishCopyLoadingKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [editReferenceDragging, setEditReferenceDragging] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const editReferenceInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then((config) => {
        if (cancelled) return;
        setImageScale(normalizeImageScale(config.default_size));
        setImageQuality(config.default_quality || 'auto');
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isSupportedImagePreset(imageScale, aspectRatio)) {
      setImageScale('2K');
    }
  }, [aspectRatio, imageScale]);

  useEffect(() => {
    if (!productImage) {
      setProductPreview(null);
      return;
    }
    const preview = { name: productImage.name, url: URL.createObjectURL(productImage) };
    setProductPreview(preview);
    return () => URL.revokeObjectURL(preview.url);
  }, [productImage]);

  useEffect(() => {
    const previews = editReferenceFiles.map((file) => ({ name: file.name, url: URL.createObjectURL(file) }));
    setEditReferencePreviews(previews);
    return () => previews.forEach((preview) => URL.revokeObjectURL(preview.url));
  }, [editReferenceFiles]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const data = await getHistory({ limit: 100 });
      setHistoryItems(data.items.filter((item) => Boolean(item.task_request?.ecommerce)));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadHistory().catch(() => undefined);
  }, [loadHistory]);

  const mergedHistory = mergeHistoryItems([
    ...taskHistoryItems.filter((item) => Boolean(item.task_request?.ecommerce)),
    ...historyItems,
  ]);
  const groups = groupHistoryItems(mergedHistory).filter((group) => group.isEcommerce);
  const selectedGroup = groups.find((group) => group.key === selectedGroupKey) || null;

  const selectImageFile = useCallback(
    (files: File[]) => {
      const image = files.find((file) => IMAGE_TYPES.includes(file.type));
      if (!image) {
        setError(t('home_ref_image_invalid'));
        return;
      }
      setProductImage(image);
      setError('');
      setMessage(t('home_ecom_image_ready'));
    },
    [t],
  );

  const addEditReferenceFiles = useCallback(
    (files: File[]) => {
      const images = files.filter((file) => IMAGE_TYPES.includes(file.type));
      if (images.length === 0) {
        setError(t('home_ref_image_invalid'));
        return;
      }
      setEditReferenceFiles((current) => [...current, ...images].slice(0, 6));
      setError('');
    },
    [t],
  );

  function handleProductImageChange(event: ChangeEvent<HTMLInputElement>) {
    selectImageFile(Array.from(event.target.files || []));
    event.target.value = '';
  }

  function handleEditReferenceChange(event: ChangeEvent<HTMLInputElement>) {
    addEditReferenceFiles(Array.from(event.target.files || []));
    event.target.value = '';
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (!Array.from(event.dataTransfer.types).includes('Files')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setDragging(true);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    selectImageFile(Array.from(event.dataTransfer.files || []));
  }

  function handleEditReferenceDragOver(event: DragEvent<HTMLDivElement>) {
    if (!Array.from(event.dataTransfer.types).includes('Files')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setEditReferenceDragging(true);
  }

  function handleEditReferenceDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setEditReferenceDragging(false);
    addEditReferenceFiles(Array.from(event.dataTransfer.files || []));
  }

  async function handleSubmit() {
    if (!productImage || loading) {
      if (!productImage) setError(t('home_ecom_missing_image'));
      return;
    }
    setLoading(true);
    setError('');
    setMessage(t('home_ecom_sent'));
    try {
      const task = await generateEcommerceImages(
        {
          product_name: form.productName,
          materials: form.materials,
          selling_points: form.sellingPoints,
          scenarios: form.scenarios,
          platform: form.platform,
          style: form.style,
          extra_requirements: form.extraRequirements,
          size: providerImageSize(imageScale, aspectRatio),
          aspect_ratio: aspectRatio,
          quality: imageQuality,
          n: Math.max(1, Math.min(9, Number(imageCount) || 4)),
        },
        productImage,
      );
      addTask(task);
      openDrawer();
      setMessage(t('home_message_processing'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setMessage('');
    } finally {
      setLoading(false);
    }
  }

  async function handleDeleteGroup(group: HistoryGroup) {
    const ids = group.items.map((item) => item.id);
    await Promise.all(ids.map((id) => deleteHistory(id)));
    setHistoryItems((current) => current.filter((item) => !ids.includes(item.id)));
    if (selectedGroupKey === group.key) setSelectedGroupKey(null);
  }

  async function handleDeleteItem(item: HistoryItem) {
    await deleteHistory(item.id);
    setHistoryItems((current) => current.filter((historyItem) => historyItem.id !== item.id));
  }

  async function openProject(group: HistoryGroup) {
    const ecommerce = group.first.task_request?.ecommerce;
    if (ecommerce) {
      setForm({
        productName: ecommerce.product_name || '',
        materials: ecommerce.materials || '',
        sellingPoints: ecommerce.selling_points || '',
        scenarios: ecommerce.scenarios || '',
        platform: ecommerce.platform || '淘宝/抖音',
        style: ecommerce.style || '高级、干净、统一电商详情页',
        extraRequirements: ecommerce.extra_requirements || '',
      });
    }
    setImageScale(normalizeImageScale(group.first.size));
    setAspectRatio(group.first.aspect_ratio || '1:1');
    setImageQuality(group.first.quality || 'auto');
    setImageCount(String(Math.max(1, Math.min(9, group.images.length || group.items.length || 1))));
    setSelectedGroupKey(group.key);
    setMessage(t('ecom_form_restored'));

    if (!group.first.input_image_url) {
      setProductImage(null);
      return;
    }
    try {
      const response = await fetch(group.first.input_image_url, { credentials: 'include' });
      if (!response.ok) throw new Error(response.statusText);
      const blob = await response.blob();
      const contentType = blob.type || 'image/png';
      const extension = contentType.includes('jpeg') || contentType.includes('jpg') ? 'jpg' : contentType.includes('webp') ? 'webp' : 'png';
      const productName = ecommerce?.product_name?.trim() || group.title || 'product';
      const file = new File([blob], `${productName}.${extension}`, { type: contentType });
      setProductImage(file);
    } catch {
      setProductImage(null);
    }
  }

  function beginEdit(item: HistoryItem) {
    setEditingItem(item);
    setEditPrompt(item.prompt);
    setEditReferenceFiles([]);
  }

  async function submitEdit() {
    if (!editingItem || !editPrompt.trim()) return;
    setLoading(true);
    setError('');
    try {
      const task = await editHistoryImage(editingItem.id, {
        prompt: editPrompt.trim(),
        size: editingItem.size,
        aspect_ratio: editingItem.aspect_ratio,
        quality: editingItem.quality,
      }, editReferenceFiles);
      addTask(task);
      openDrawer();
      setEditingItem(null);
      setEditPrompt('');
      setEditReferenceFiles([]);
      setMessage(t('home_message_edit_sent'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function copyPrompt(prompt: string) {
    await copyTextToClipboard(prompt);
    setMessage(t('home_prompt_copied'));
  }

  async function copyPublishText(text: string) {
    await copyTextToClipboard(text);
    setMessage(t('ecom_publish_copied'));
  }

  async function generatePublishCopy(group: HistoryGroup) {
    const ecommerce = group.first.task_request?.ecommerce;
    setPublishCopyLoadingKey(group.key);
    setError('');
    try {
      const copy = await generateEcommercePublishCopy({
        product_name: ecommerce?.product_name || group.title,
        materials: ecommerce?.materials || '',
        selling_points: ecommerce?.selling_points || '',
        scenarios: ecommerce?.scenarios || '',
        platform: ecommerce?.platform || '',
        style: ecommerce?.style || '',
        extra_requirements: ecommerce?.extra_requirements || '',
        image_count: group.images.length || group.items.length || 1,
        size: group.first.size,
        aspect_ratio: group.first.aspect_ratio,
      });
      setPublishCopies((current) => ({ ...current, [group.key]: copy }));
      setMessage(t('ecom_publish_generated'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPublishCopyLoadingKey(null);
    }
  }

  return (
    <div className="mx-auto min-h-screen max-w-[1440px] bg-[radial-gradient(ellipse_at_top,var(--color-surface-container-high),var(--color-background))] px-4 pb-14 pt-24 font-mono sm:px-6">
      <div className="mb-6 flex flex-col gap-3 border-b border-white/10 pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-secondary">
            <span className="h-[1px] w-4 bg-secondary" />
            {t('ecom_tag')}
          </div>
          <h1 className="text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">{t('ecom_title')}</h1>
          <p className="mt-2 text-sm text-white/50">{t('ecom_subtitle')}</p>
        </div>
        <div className="grid grid-cols-2 gap-2 md:w-[320px]">
          <Link className="flex h-10 items-center justify-center border border-primary/35 text-xs font-bold uppercase tracking-widest text-primary hover:bg-primary/10" to="/">
            {t('home_tab_general')}
          </Link>
          <button className="h-10 border border-secondary bg-secondary/15 text-xs font-bold uppercase tracking-widest text-secondary" type="button">
            {t('home_tab_ecommerce')}
          </button>
        </div>
      </div>

      {error ? <div className="mb-4 border border-error/40 bg-error/10 p-3 text-xs text-error">{error}</div> : null}
      {message ? <div className="mb-4 border border-primary/25 bg-primary/5 p-3 text-xs uppercase tracking-widest text-primary">{message}</div> : null}

      <section
        className={`mb-8 grid grid-cols-1 gap-3 border bg-black/55 p-3 transition-colors lg:grid-cols-[220px_1fr_auto] ${
          dragging ? 'border-secondary bg-secondary/10' : 'border-primary/20'
        }`}
        onDragEnter={handleDragOver}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
        }}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
      >
        <div className="min-w-0">
          <input ref={fileInputRef} className="hidden" type="file" accept="image/png,image/jpeg,image/webp" onChange={handleProductImageChange} />
          <div className="relative">
            <button
              className="group relative flex h-32 w-full items-center justify-center overflow-hidden border border-dashed border-primary/25 bg-black hover:bg-primary/5"
              type="button"
              onClick={() => fileInputRef.current?.click()}
            >
              {productPreview ? (
                <>
                  <img alt={productPreview.name} className="h-full w-full object-cover opacity-90" src={productPreview.url} />
                  <span className="absolute bottom-0 left-0 right-0 truncate bg-black/75 px-2 py-1 text-[9px] text-white/70">{productPreview.name}</span>
                </>
              ) : (
                <div className="flex flex-col items-center gap-2 text-white/40 group-hover:text-primary">
                  <ImagePlus size={24} />
                  <span className="text-[10px] uppercase tracking-widest">{t('home_ecom_upload_tip')}</span>
                </div>
              )}
            </button>
            {productPreview ? (
              <button
                className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center border border-white/15 bg-black/80 text-white/80 hover:border-error hover:text-error"
                type="button"
                onClick={() => {
                  setProductImage(null);
                  setMessage(t('home_ecom_image_removed'));
                }}
              >
                <X size={14} />
              </button>
            ) : null}
          </div>
        </div>

        <div className="grid min-w-0 grid-cols-2 gap-2 lg:grid-cols-4">
          <CompactInput label={t('home_ecom_product_name')} value={form.productName} onChange={(value) => setForm((current) => ({ ...current, productName: value }))} />
          <CompactInput label={t('home_ecom_platform')} value={form.platform} onChange={(value) => setForm((current) => ({ ...current, platform: value }))} />
          <CompactInput label={t('home_ecom_style')} value={form.style} onChange={(value) => setForm((current) => ({ ...current, style: value }))} />
          <GenerationSelect label={t('home_image_count')} value={imageCount} onChange={setImageCount} options={IMAGE_COUNT_OPTIONS} />
          <CompactInput label={t('home_ecom_materials')} value={form.materials} onChange={(value) => setForm((current) => ({ ...current, materials: value }))} />
          <CompactInput label={t('home_ecom_selling_points')} value={form.sellingPoints} onChange={(value) => setForm((current) => ({ ...current, sellingPoints: value }))} />
          <CompactInput label={t('home_ecom_scenarios')} value={form.scenarios} onChange={(value) => setForm((current) => ({ ...current, scenarios: value }))} />
          <GenerationSelect label={t('home_size')} value={imageScale} onChange={setImageScale} options={SIZE_OPTIONS} getOptionLabel={(option) => SIZE_LABELS[option] || option} isOptionDisabled={(option) => !isSupportedImagePreset(option, aspectRatio)} />
          <GenerationSelect label={t('home_aspect_ratio')} value={aspectRatio} onChange={setAspectRatio} options={ASPECT_RATIO_OPTIONS} />
          <GenerationSelect label={t('home_quality')} value={imageQuality} onChange={setImageQuality} options={QUALITY_OPTIONS} />
          <label className="col-span-2 min-w-0 lg:col-span-2">
            <span className="mb-0.5 block truncate text-[8px] uppercase tracking-[0.18em] text-white/40">{t('home_ecom_extra')}</span>
            <input
              className="h-9 w-full border border-primary/20 bg-black px-2 text-xs text-primary outline-none focus:border-primary"
              value={form.extraRequirements}
              onChange={(event) => setForm((current) => ({ ...current, extraRequirements: event.target.value }))}
            />
          </label>
        </div>

        <button
          className="flex h-12 items-center justify-center bg-primary px-5 text-sm font-black uppercase tracking-widest text-black shadow-[0_0_15px_rgba(0,243,255,0.4)] transition-transform hover:scale-95 disabled:opacity-40 lg:h-full lg:w-32"
          type="button"
          disabled={loading || !productImage}
          onClick={handleSubmit}
        >
          {loading ? <Loader2 className="animate-spin" size={22} /> : t('home_execute')}
        </button>
      </section>

      {selectedGroup ? (
        <section>
          <button
            className="mb-4 flex h-10 items-center gap-2 border border-primary/25 px-4 text-xs uppercase tracking-widest text-primary hover:bg-primary/10"
            type="button"
            onClick={() => setSelectedGroupKey(null)}
          >
            <ArrowLeft size={14} />
            {t('ecom_back_projects')}
          </button>
          <ProjectDetail
            group={selectedGroup}
            onPreview={setPreviewItem}
            onDeleteItem={(item) => handleDeleteItem(item).catch((err) => setError(err instanceof Error ? err.message : String(err)))}
            onEdit={beginEdit}
            onCopy={(prompt) => copyPrompt(prompt).catch(() => undefined)}
            onCopyText={(text) => copyPublishText(text).catch(() => undefined)}
            publishCopy={publishCopies[selectedGroup.key] || null}
            publishCopyLoading={publishCopyLoadingKey === selectedGroup.key}
            onGeneratePublishCopy={() => generatePublishCopy(selectedGroup)}
            t={t}
          />
        </section>
      ) : (
        <section>
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-xl font-black tracking-tight text-white">{t('ecom_projects')}</h2>
            <button className="flex h-9 items-center gap-2 border border-white/10 px-3 text-[10px] uppercase tracking-widest text-white/60 hover:border-primary hover:text-primary" type="button" onClick={() => loadHistory().catch(() => undefined)}>
              {historyLoading ? <Loader2 className="animate-spin" size={13} /> : <RefreshCw size={13} />}
              {t('config_sync_cases')}
            </button>
          </div>
          {groups.length > 0 ? (
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
              {groups.map((group) => (
                <ProjectCard
                  key={group.key}
                  group={group}
                  onOpen={() => openProject(group).catch((err) => setError(err instanceof Error ? err.message : String(err)))}
                  onDelete={() => handleDeleteGroup(group).catch((err) => setError(err instanceof Error ? err.message : String(err)))}
                  t={t}
                />
              ))}
            </div>
          ) : (
            <div className="flex min-h-[260px] items-center justify-center border border-primary/20 bg-black/50 px-6 text-center text-sm text-white/45">
              {historyLoading ? t('home_loading_feed') : t('ecom_empty')}
            </div>
          )}
        </section>
      )}

      <ImagePreviewModal
        imageUrl={previewItem?.imageUrl || null}
        alt={previewItem?.prompt || 'preview'}
        subtitle={previewItem?.referenceUrl ? `${previewItem.prompt}\n\n${t('ecom_reference_image')}: ${previewItem.referenceUrl}` : previewItem?.prompt}
        onClose={() => setPreviewItem(null)}
      />
      <PromptEditorModal
        open={Boolean(editingItem)}
        value={editPrompt}
        onChange={setEditPrompt}
        onClose={() => {
          setEditingItem(null);
          setEditPrompt('');
          setEditReferenceFiles([]);
        }}
        onCopy={() => copyPrompt(editPrompt).catch(() => undefined)}
      />
      {editingItem ? (
        <div className="fixed bottom-4 left-3 right-3 z-[230] flex flex-col gap-2 sm:left-auto sm:right-5 sm:w-[420px]">
          <div
            className={`border bg-black/95 p-3 shadow-[0_0_24px_rgba(0,0,0,0.45)] backdrop-blur ${
              editReferenceDragging ? 'border-secondary bg-secondary/10' : 'border-primary/30'
            }`}
            onDragEnter={handleEditReferenceDragOver}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setEditReferenceDragging(false);
            }}
            onDragOver={handleEditReferenceDragOver}
            onDrop={handleEditReferenceDrop}
          >
            <input
              ref={editReferenceInputRef}
              className="hidden"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              onChange={handleEditReferenceChange}
            />
            <div className="mb-2 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[10px] font-bold uppercase tracking-widest text-secondary">{t('ecom_edit_references')}</div>
                <div className="mt-1 text-xs leading-5 text-white/55">{t('ecom_edit_reference_tip')}</div>
              </div>
              <button
                className="flex h-9 shrink-0 items-center gap-2 border border-primary/35 px-3 text-[10px] font-bold uppercase tracking-widest text-primary hover:bg-primary/10"
                type="button"
                onClick={() => editReferenceInputRef.current?.click()}
              >
                <Paperclip size={13} />
                {t('ecom_add_reference')}
              </button>
            </div>
            {editReferencePreviews.length > 0 ? (
              <div className="flex gap-2 overflow-x-auto pb-1">
                {editReferencePreviews.map((preview, index) => (
                  <div key={`${preview.name}-${preview.url}`} className="relative h-16 w-16 shrink-0 overflow-hidden border border-white/10 bg-black">
                    <img alt={preview.name} className="h-full w-full object-cover" src={preview.url} />
                    <button
                      className="absolute right-0 top-0 flex h-6 w-6 items-center justify-center bg-black/80 text-white/80 hover:text-error"
                      type="button"
                      onClick={() => setEditReferenceFiles((current) => current.filter((_, currentIndex) => currentIndex !== index))}
                    >
                      <X size={12} />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
          <button
            className="flex h-12 items-center justify-center gap-2 bg-primary px-5 text-xs font-black uppercase tracking-widest text-black shadow-[0_0_20px_rgba(0,243,255,0.45)] hover:bg-white disabled:opacity-40"
            type="button"
            disabled={loading || !editPrompt.trim()}
            onClick={submitEdit}
          >
            {loading ? <Loader2 className="animate-spin" size={16} /> : <PencilLine size={16} />}
            {t('ecom_edit_this_image')}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ProjectCard({ group, onOpen, onDelete, t }: { key?: string; group: HistoryGroup; onOpen: () => void; onDelete: () => void; t: (key: any, vars?: Record<string, string | number>) => string }) {
  return (
    <div className="overflow-hidden border border-primary/25 bg-black transition-colors hover:border-secondary/50">
      <button className="grid w-full grid-cols-3 gap-1 bg-black p-1 text-left" type="button" onClick={onOpen}>
        {group.images.slice(0, 9).map((image) => (
          <div key={image.id} className="aspect-square overflow-hidden bg-black">
            <img alt={group.title} className="h-full w-full object-cover opacity-95" loading="lazy" src={image.url} />
          </div>
        ))}
      </button>
      <div className="border-t border-white/10 bg-surface-container-low/80 p-4">
        <div className="mb-2 flex items-center justify-between gap-3">
          <h3 className="min-w-0 truncate text-lg font-black text-white">{group.title}</h3>
          <span className="shrink-0 text-[10px] uppercase tracking-widest text-secondary">x{group.images.length}</span>
        </div>
        <div className="mb-3 flex flex-wrap gap-2 text-[10px] uppercase tracking-widest text-white/40">
          <span>{formatDate(group.createdAt)}</span>
          <span>{group.first.size}</span>
          {group.first.aspect_ratio ? <span>{group.first.aspect_ratio}</span> : null}
        </div>
        <p className="mb-3 line-clamp-2 text-sm text-white/65">{group.taskPrompt}</p>
        <div className="grid grid-cols-[1fr_44px] gap-2">
          <button className="h-10 bg-primary text-xs font-black uppercase tracking-widest text-black hover:bg-white" type="button" onClick={onOpen}>
            {t('ecom_open_project')}
          </button>
          <button className="flex h-10 items-center justify-center border border-error/25 bg-error/5 text-error hover:bg-error/15" type="button" onClick={onDelete}>
            <Trash2 size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

function ProjectDetail({
  group,
  onPreview,
  onDeleteItem,
  onEdit,
  onCopy,
  onCopyText,
  publishCopy,
  publishCopyLoading,
  onGeneratePublishCopy,
  t,
}: {
  group: HistoryGroup;
  onPreview: (item: { imageUrl: string | null; prompt: string; referenceUrl?: string | null }) => void;
  onDeleteItem: (item: HistoryItem) => void;
  onEdit: (item: HistoryItem) => void;
  onCopy: (prompt: string) => void;
  onCopyText: (text: string) => void;
  publishCopy: EcommercePublishCopyResult | null;
  publishCopyLoading: boolean;
  onGeneratePublishCopy: () => void;
  t: (key: any, vars?: Record<string, string | number>) => string;
}) {
  const referenceUrl = group.first.input_image_url;
  return (
    <>
      <div className="mb-5 grid grid-cols-1 gap-4 border border-primary/20 bg-black/50 p-4 md:grid-cols-[160px_1fr]">
        <button
          className="flex h-40 items-center justify-center overflow-hidden border border-dashed border-secondary/30 bg-black text-xs uppercase tracking-widest text-white/35"
          type="button"
          disabled={!referenceUrl}
          onClick={() => onPreview({ imageUrl: referenceUrl || null, prompt: t('ecom_reference_image') })}
        >
          {referenceUrl ? <img alt={t('ecom_reference_image')} className="h-full w-full object-cover" src={referenceUrl} /> : t('ecom_no_reference')}
        </button>
        <div className="min-w-0">
          <div className="mb-2 text-[10px] uppercase tracking-widest text-secondary">{t('ecom_project_detail')}</div>
          <h2 className="text-3xl font-black tracking-tight text-white">{group.title}</h2>
          <p className="mt-3 line-clamp-4 text-sm text-white/70">{group.taskPrompt}</p>
          <div className="mt-4 flex flex-wrap gap-2 text-[10px] uppercase tracking-widest text-white/40">
            <span>{group.first.model}</span>
            <span>{group.first.size}</span>
            {group.first.aspect_ratio ? <span>{group.first.aspect_ratio}</span> : null}
            <span>{group.first.quality}</span>
            <span>x{group.images.length}</span>
          </div>
        </div>
      </div>

      <div className="mb-5 border border-primary/20 bg-black/55 p-4">
        <div className="mb-3 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="mb-1 flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-secondary">
              <Sparkles size={14} />
              {t('ecom_publish_material')}
            </div>
            <p className="text-xs text-white/45">{t('ecom_publish_hint')}</p>
          </div>
          <div className="grid grid-cols-1 gap-2 md:w-[360px]">
            <button
              className="flex h-10 items-center justify-center gap-2 bg-secondary text-[10px] font-black uppercase tracking-widest text-black hover:bg-white disabled:opacity-50"
              type="button"
              disabled={publishCopyLoading}
              onClick={onGeneratePublishCopy}
            >
              {publishCopyLoading ? <Loader2 className="animate-spin" size={14} /> : <Sparkles size={14} />}
              {publishCopy ? t('ecom_regenerate_publish') : t('ecom_generate_publish')}
            </button>
          </div>
        </div>
        {publishCopy ? (
          <div>
            <div className="mb-3 grid grid-cols-3 gap-2 md:w-[300px]">
              <button className="h-9 border border-primary/30 text-[10px] font-bold uppercase tracking-widest text-primary hover:bg-primary/10" type="button" onClick={() => onCopyText(publishCopy.title)}>
                {t('ecom_copy_title')}
              </button>
              <button className="h-9 border border-primary/30 text-[10px] font-bold uppercase tracking-widest text-primary hover:bg-primary/10" type="button" onClick={() => onCopyText(publishCopy.body)}>
                {t('ecom_copy_body')}
              </button>
              <button className="h-9 border border-secondary bg-secondary/15 text-[10px] font-black uppercase tracking-widest text-secondary hover:bg-secondary hover:text-black" type="button" onClick={() => onCopyText(`${publishCopy.title}\n\n${publishCopy.body}`)}>
                {t('ecom_copy_all')}
              </button>
            </div>
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[320px_1fr]">
              <div className="border border-white/10 bg-white/[0.03] p-3">
                <div className="mb-2 text-[9px] uppercase tracking-widest text-white/35">{t('ecom_publish_title')}</div>
                <p className="text-sm font-bold leading-6 text-white">{publishCopy.title}</p>
              </div>
              <div className="border border-white/10 bg-white/[0.03] p-3">
                <div className="mb-2 text-[9px] uppercase tracking-widest text-white/35">{t('ecom_publish_body')}</div>
                <p className="whitespace-pre-wrap text-xs leading-6 text-white/75">{publishCopy.body}</p>
              </div>
            </div>
          </div>
        ) : (
          <div className="border border-dashed border-white/10 bg-white/[0.02] p-4 text-xs leading-6 text-white/45">{t('ecom_publish_empty')}</div>
        )}
      </div>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
        {group.images.map((image, index) => (
          <div key={image.id} className="overflow-hidden border border-white/10 bg-black">
            <button className="block w-full cursor-zoom-in bg-black text-left" type="button" onClick={() => onPreview({ imageUrl: image.url, prompt: image.prompt, referenceUrl })}>
              <img alt={`${group.title}-${index + 1}`} className="block h-auto w-full opacity-95 hover:opacity-100" loading="lazy" src={image.url} />
            </button>
            <div className="border-t border-white/10 bg-surface-container-low/80 p-4">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="text-[10px] uppercase tracking-widest text-secondary">{t('ecom_screen_index', { value: index + 1 })}</div>
                <div className="text-[10px] text-white/30">ID:{image.id.slice(0, 4).toUpperCase()}</div>
              </div>
              <p className="mb-3 line-clamp-4 text-sm text-white/75">{image.prompt}</p>
              <div className="grid grid-cols-[44px_44px_44px_1fr] gap-2">
                <button className="flex h-10 items-center justify-center border border-white/15 bg-white/5 text-white/75 hover:border-primary hover:text-primary" type="button" onClick={() => onPreview({ imageUrl: image.url, prompt: image.prompt, referenceUrl })}>
                  <Maximize2 size={14} />
                </button>
                <a className="flex h-10 items-center justify-center border border-white/15 bg-white/5 text-white/75 hover:border-primary hover:text-primary" href={image.url} download>
                  <Download size={14} />
                </a>
                <button className="flex h-10 items-center justify-center border border-error/20 bg-error/5 text-error hover:bg-error/15" type="button" onClick={() => onDeleteItem(image.item)}>
                  <Trash2 size={14} />
                </button>
                <button className="flex h-10 min-w-0 items-center justify-center gap-2 bg-primary px-3 text-xs font-black uppercase text-black hover:bg-white" type="button" onClick={() => onEdit(image.item)}>
                  <PencilLine size={14} />
                  {t('ecom_edit_this_image')}
                </button>
              </div>
              <button className="mt-2 h-9 w-full border border-secondary/30 text-[10px] font-bold uppercase tracking-widest text-secondary hover:bg-secondary/10" type="button" onClick={() => onCopy(image.prompt)}>
                {t('home_clone_prompt')}
              </button>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
