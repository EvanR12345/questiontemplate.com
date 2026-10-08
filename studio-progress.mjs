// Progress is stable between project revisions. Queue polling must not scan every
// shot or recreate a large flattened image list on each update.
export function createProgressReader(decision){
 let previous,revision,summary;
 return {clear(){previous=null;},read(project){
  if(previous===project&&revision===project.revision)return summary;
  summary={imageTotal:0,imageDone:0,blocking:0,failures:0};
  for(const chapter of project.chapters)for(const scene of chapter.scenes)for(const shot of scene.shots){
   summary.imageTotal++;
   if(shot.imagePath)summary.imageDone++;
   const blocking=Boolean(decision(shot).blocking);
   if(blocking)summary.blocking++;
   if(blocking||(shot.generationError&&!shot.imagePath))summary.failures++;
  }
  previous=project;revision=project.revision;return summary;
 }};
}
